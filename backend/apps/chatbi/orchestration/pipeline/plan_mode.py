"""PLAN 模式：多 QueryTask 规划、校验与顺序执行。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from apps.chatbi.models.dto.analysis_plan import (
    AnalysisPlan,
    AnalysisPlanStatus,
    CompiledQuery,
    ComputeTask,
    QueryTask,
    ResultSetKind,
    ResultSetRef,
)
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tool_results import ChatBIToolResultProcessor
from apps.chatbi.orchestration.pipeline.events import PipelineEvents
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.computation import ComputeEngine, ComputeEngineError
from apps.chatbi.services.generation.agent_finalization import (
    AgentFinalizationInput,
    AgentFinalizationService,
)
from apps.chatbi.services.generation.answer_composer import (
    AnswerComposer,
    AnswerComposerInput,
)
from apps.chatbi.services.planning.analysis_planner import AnalysisPlanner
from apps.chatbi.services.planning.plan_validation import validate_analysis_plan
from apps.conversation import ChatRecordExecutionType
from apps.event import EventPublisher, RenderEvent
from apps.tool import ToolCall, ToolCallContext, ToolRegistry, ToolResult, ToolStatus
from common.observability import MetricsRecorder


class PlanPipelineError(RuntimeError):
    """PLAN 模式无法安全进入下一阶段。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True, slots=True)
class PlanPipelineDependencies:
    registry: ToolRegistry
    result_processor: ChatBIToolResultProcessor
    finalization_service: AgentFinalizationService
    lifecycle: AgentLifecycle
    event_publisher: EventPublisher
    session: Any
    max_query_tasks: int = 5
    compute_engine: ComputeEngine | None = None
    compute_enabled: bool = True
    answer_composer: AnswerComposer | None = None
    metrics: MetricsRecorder | None = None


class PlanPipeline:
    """PLAN 的规则规划和多查询执行通道。"""

    def __init__(self, dependencies: PlanPipelineDependencies) -> None:
        self._registry = dependencies.registry
        self._result_processor = dependencies.result_processor
        self._finalization_service = dependencies.finalization_service
        self._lifecycle = dependencies.lifecycle
        self._events = PipelineEvents(dependencies.event_publisher)
        self._session = dependencies.session
        self._planner = AnalysisPlanner(max_query_tasks=dependencies.max_query_tasks)
        self._max_query_tasks = dependencies.max_query_tasks
        self._compute_engine = dependencies.compute_engine
        self._compute_enabled = dependencies.compute_enabled
        self._answer_composer = dependencies.answer_composer
        self._metrics = dependencies.metrics

    def run(self, state: AgentRuntimeState) -> Iterator[RenderEvent]:
        run_id = state.require_run_id()
        if self._metrics is not None:
            self._metrics.record_run(mode="plan", status="started")
        understanding = state.context.state.get("question_understanding")
        if not isinstance(understanding, dict):
            raise PlanPipelineError("PLAN_QUESTION_UNDERSTANDING_REQUIRED")
        if state.context.semantic_asset_scope is None:
            self._call_tool(state, "search_semantic_assets", {})
        plan_id = f"plan-{run_id}"
        patched_payload = (
            understanding.get("inherited_context", {}).get("patched_analysis_plan")
            if isinstance(understanding.get("inherited_context"), dict)
            else None
        )
        if isinstance(patched_payload, dict):
            # 补丁计划已经在问题理解阶段完成服务端校验，换用当前 Run 的计划编号。
            patched = AnalysisPlan.model_validate(patched_payload)
            plan = patched.model_copy(update={"id": plan_id})
        else:
            plan = self._planner.plan_from_semantic_state(
                plan_id=plan_id,
                question_understanding=understanding,
                semantic_state=state.context.state,
            )
        yield self._events.plan_created(
            run_id,
            {
                "record_id": state.record.id,
                "run_id": run_id,
                "plan_id": plan_id,
                "status": "DRAFT",
            },
        )
        self._save_plan(state, plan)
        self._session.commit()
        if plan.validation.status is AnalysisPlanStatus.REJECTED:
            raise PlanPipelineError(
                "PLAN_VALIDATION_FAILED",
                ",".join(plan.validation.reason_codes),
            )
        query_tasks = [task for task in plan.tasks if isinstance(task, QueryTask)]
        if not query_tasks:
            raise PlanPipelineError("PLAN_QUERY_TASK_REQUIRED")

        execution_records: dict[str, dict[str, Any]] = {}
        full_data_records: dict[str, list[dict[str, Any]]] = {}
        completed_tasks: dict[str, QueryTask] = {}
        for task in query_tasks:
            yield self._events.task_started(
                run_id,
                {
                    "record_id": state.record.id,
                    "run_id": run_id,
                    "plan_id": plan_id,
                    "task_id": task.id,
                    "status": "running",
                },
            )
            compiled = self._compile_task(state, task)
            completed_tasks[task.id] = task.model_copy(update={"compiled": compiled})
            self._save_plan(
                state,
                self._plan_with_completed_queries(plan, completed_tasks),
            )
            state.context.state["result_node_id"] = task.id
            self._call_tool(state, "validate_sql", {"sql": compiled.sql})
            self._call_tool(state, "execute_sql", {"sql": compiled.sql})
            self._persist_state(state)
            execution = state.context.state.get("last_execution")
            execution_records[task.id] = execution if isinstance(execution, dict) else {}
            full_data = state.context.state.get("full_data")
            if isinstance(full_data, list):
                full_data_records[task.id] = [
                    row for row in full_data if isinstance(row, dict)
                ]
            yield self._events.task_finished(
                run_id,
                {
                    "record_id": state.record.id,
                    "run_id": run_id,
                    "plan_id": plan_id,
                    "task_id": task.id,
                    "result_set_id": (execution or {}).get("result_set_id"),
                    "status": "succeeded",
                },
            )

        compute_tasks = [task for task in plan.tasks if isinstance(task, ComputeTask)]
        pending_compute_tasks = list(compute_tasks)
        while pending_compute_tasks:
            result_sets = state.context.state.get("result_sets")
            available_nodes = {
                payload.get("node_id")
                for payload in (result_sets.values() if isinstance(result_sets, dict) else ())
                if isinstance(payload, dict)
            }
            ready_tasks = [
                task
                for task in pending_compute_tasks
                if all(input_id in available_nodes for input_id in task.inputs)
            ]
            if not ready_tasks:
                raise PlanPipelineError("PLAN_COMPUTE_DAG_UNRESOLVED")
            for compute_task in ready_tasks:
                yield self._events.task_started(
                    run_id,
                    {
                        "record_id": state.record.id,
                        "run_id": run_id,
                        "plan_id": plan_id,
                        "task_id": compute_task.id,
                        "status": "running",
                    },
                )
                execution = self._execute_compute_task(state, compute_task)
                execution_records[compute_task.id] = execution
                full_data = state.context.state.get("full_data")
                if isinstance(full_data, list):
                    full_data_records[compute_task.id] = [
                        row for row in full_data if isinstance(row, dict)
                    ]
                yield self._events.compute_finished(
                    run_id,
                    {
                        "record_id": state.record.id,
                        "run_id": run_id,
                        "plan_id": plan_id,
                        "task_id": compute_task.id,
                        "result_set_id": execution.get("result_set_id"),
                        "status": "succeeded",
                    },
                )
                yield self._events.task_finished(
                    run_id,
                    {
                        "record_id": state.record.id,
                        "run_id": run_id,
                        "plan_id": plan_id,
                        "task_id": compute_task.id,
                        "result_set_id": execution.get("result_set_id"),
                        "status": "succeeded",
                    },
                )
            pending_compute_tasks = [
                task for task in pending_compute_tasks if task not in ready_tasks
            ]

        final_plan = plan.model_copy(
            update={
                "tasks": tuple(
                    completed_tasks.get(task.id, task) for task in plan.tasks
                ),
                "validation": validate_analysis_plan(
                    plan.model_copy(
                        update={
                            "tasks": tuple(
                                completed_tasks.get(task.id, task)
                                for task in plan.tasks
                            )
                        }
                    ),
                    max_query_tasks=self._max_query_tasks,
                    require_proven=True,
                ),
            }
        )
        self._save_plan(state, final_plan)
        self._session.commit()
        yield self._events.plan_updated(
            run_id,
            {
                "record_id": state.record.id,
                "run_id": run_id,
                "plan_id": plan_id,
                "status": final_plan.validation.status.value,
            },
        )

        primary_result_id = plan.presentation.primary_result
        primary_execution = execution_records.get(primary_result_id)
        if primary_execution is None:
            raise PlanPipelineError("PLAN_PRIMARY_RESULT_MISSING")
        primary_rows = primary_execution.get("sample_rows") or []
        primary_full_data = full_data_records.get(primary_result_id, primary_rows)
        state.context.state["last_execution"] = primary_execution
        state.context.state["full_data"] = primary_full_data
        intent = understanding.get("intent")
        question = str(
            state.context.state.get("question") or state.record.question or ""
        )
        if self._answer_composer is not None:
            final = self._answer_composer.compose(
                AnswerComposerInput(
                    question=question,
                    intent=intent if isinstance(intent, dict) else {},
                    execution=primary_execution,
                    rows=primary_full_data,
                    plan=state.context.state.get("analysis_plan") if isinstance(state.context.state.get("analysis_plan"), dict) else {},
                    semantic_context=state.context.state.get("semantic_scope") if isinstance(state.context.state.get("semantic_scope"), dict) else {},
                    mode="plan",
                )
            )
        else:
            final = self._finalization_service.generate(
                AgentFinalizationInput(
                    question=question,
                    intent=intent if isinstance(intent, dict) else {},
                    execution=primary_execution,
                    rows=primary_rows,
                )
            )
        yield from self._lifecycle.finish(
            state,
            answer=final.answer,
            chart=final.chart,
            sql=primary_execution.get("sql"),
            full_data=primary_full_data,
            execution=primary_execution,
            claims=list(getattr(final, "claims", []) or []),
            caliber_card=dict(getattr(final, "caliber_card", {}) or {}),
            chart_spec=dict(getattr(final, "chart_spec", {}) or {}),
        )

    def _compile_task(self, state: AgentRuntimeState, task: QueryTask) -> CompiledQuery:
        scope = state.context.semantic_asset_scope
        if scope is None or scope.semantic_enforcement == "STRICT" or scope.query_plan is not None:
            raise PlanPipelineError("PLAN_STRICT_MULTI_QUERY_NOT_READY")
        compile_plan = scope.compile_plan
        if compile_plan is None:
            raise PlanPipelineError("PLAN_COMPILE_PLAN_REQUIRED")
        subset = compile_plan.model_copy(
            update={
                "metric_asset_ids": task.spec.metric_ids,
                "dimension_asset_ids": task.spec.dimension_ids,
                "limit": task.spec.limit or compile_plan.limit,
                "having": task.spec.having or compile_plan.having,
                "time_offset": task.spec.time_offset or compile_plan.time_offset,
            }
        )
        state.context.state["semantic_scope"] = scope.model_copy(
            update={"compile_plan": subset}
        ).model_dump(mode="json")
        try:
            result = self._call_tool(state, "compile_semantic_sql", {})
        finally:
            state.context.state["semantic_scope"] = scope.model_dump(mode="json")
        data = result.data
        if data is None:
            raise PlanPipelineError("PLAN_COMPILE_RESULT_MISSING")
        payload = data.model_dump(mode="json")
        return CompiledQuery(
            plan_fingerprint=f"{scope.retrieval_id}:{task.id}",
            sql=str(payload["sql"]),
            tables=tuple(str(item) for item in payload.get("tables") or []),
        )

    def _execute_compute_task(
        self,
        state: AgentRuntimeState,
        task: ComputeTask,
    ) -> dict[str, Any]:
        if not self._compute_enabled:
            raise PlanPipelineError(
                "PLAN_COMPUTE_DISABLED",
                "CHATBI_COMPUTE_ENABLED 未启用。",
            )
        if self._compute_engine is None:
            raise PlanPipelineError("PLAN_COMPUTE_ENGINE_REQUIRED")
        result_store = state.context.result_store
        if result_store is None:
            raise PlanPipelineError("PLAN_RESULT_STORE_REQUIRED")
        result_sets = state.context.state.get("result_sets")
        if not isinstance(result_sets, dict):
            raise PlanPipelineError("PLAN_INPUT_RESULT_SETS_MISSING")
        execution_id = state.context.execution_id
        chat_id = state.context.chat_id
        record_id = state.context.record_id
        if not isinstance(execution_id, str) or not execution_id:
            raise PlanPipelineError("PLAN_EXECUTION_ID_REQUIRED")
        if not isinstance(chat_id, int) or not isinstance(record_id, int):
            raise PlanPipelineError("PLAN_RESULT_OWNERSHIP_REQUIRED")
        snapshots = {}
        for input_id in task.inputs:
            result_set_id = self._result_set_id_for_node(result_sets, input_id)
            payload = result_sets.get(result_set_id) if result_set_id else None
            if not isinstance(payload, dict):
                raise PlanPipelineError("PLAN_INPUT_RESULT_SET_MISSING")
            ref = ResultSetRef.model_validate(payload)
            snapshots[input_id] = result_store.read(
                ref,
                execution_id=execution_id,
                execution_type=ChatRecordExecutionType.AGENT,
                chat_id=chat_id,
                record_id=record_id,
            )
        try:
            computed = self._compute_engine.execute(task, snapshots)
        except ComputeEngineError as exc:
            raise PlanPipelineError(exc.code, str(exc)) from exc
        result_ref = result_store.register(
            execution_id=execution_id,
            execution_type=ChatRecordExecutionType.AGENT,
            chat_id=chat_id,
            record_id=record_id,
            plan_id=str((state.context.state.get("analysis_plan") or {}).get("id") or ""),
            node_id=task.id,
            kind=ResultSetKind.COMPUTE,
            fields=list(computed.fields),
            rows=[dict(row) for row in computed.rows],
            row_count=computed.row_count,
            source_sql=computed.sql,
        )
        result_payload = result_ref.model_dump(mode="json")
        existing_result_sets = state.context.state.get("result_sets")
        if not isinstance(existing_result_sets, dict):
            existing_result_sets = {}
        state.context.state["result_sets"] = {
            **existing_result_sets,
            result_ref.result_set_id: result_payload,
        }
        execution = {
            "sql": computed.sql,
            "fields": list(computed.fields),
            "row_count": computed.row_count,
            "sample_rows": [dict(row) for row in computed.rows[:10]],
            "artifact_ref": result_ref.artifact_ref.model_dump(mode="json"),
            "result_set_id": result_ref.result_set_id,
            "sql_source": "computed",
        }
        state.context.state["last_execution"] = execution
        state.context.state["full_data"] = [dict(row) for row in computed.rows]
        self._persist_state(state)
        return execution

    @staticmethod
    def _result_set_id_for_node(
        result_sets: dict[str, Any],
        node_id: str,
    ) -> str | None:
        for result_set_id, payload in result_sets.items():
            if isinstance(payload, dict) and payload.get("node_id") == node_id:
                return str(result_set_id)
        return None

    @staticmethod
    def _plan_with_completed_queries(
        plan: AnalysisPlan,
        completed_tasks: dict[str, QueryTask],
    ) -> AnalysisPlan:
        return plan.model_copy(
            update={
                "tasks": tuple(
                    completed_tasks.get(task.id, task) for task in plan.tasks
                )
            }
        )

    def _call_tool(
        self,
        state: AgentRuntimeState,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult[Any]:
        tool = self._registry.get(name)
        if tool is None:
            raise PlanPipelineError(f"PLAN_TOOL_NOT_REGISTERED:{name}")
        call_id = f"plan:{state.require_run_id()}:{name}"
        result = self._registry.execute(
            ToolCall(name=name, args=args, call_id=call_id),
            state.context,
            call_context=ToolCallContext(
                tool_call_id=call_id,
                cancellation=state.cancellation,
            ),
        )
        projection = self._result_processor.process(state.context, name, result)
        state.context.state.update(projection.state_patch)
        if projection.result.status is not ToolStatus.SUCCEEDED:
            raise PlanPipelineError(
                projection.result.error_code or f"PLAN_{name.upper()}_FAILED",
                projection.result.model_content,
            )
        return projection.result

    @staticmethod
    def _save_plan(state: AgentRuntimeState, plan: AnalysisPlan) -> None:
        state.context.state["analysis_plan"] = plan.model_dump(mode="json")
        PlanPipeline._persist_state(state)

    @staticmethod
    def _persist_state(state: AgentRuntimeState) -> None:
        agent_run_repository.update_run(
            state.context.session,
            state.run,
            derived_state=state.persistable_context(),
        )
        state.context.session.commit()


__all__ = ["PlanPipeline", "PlanPipelineDependencies", "PlanPipelineError"]
