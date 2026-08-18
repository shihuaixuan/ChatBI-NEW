"""PLAN 模式：多 QueryTask 规划、校验与顺序执行。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from apps.chatbi.models import AgentClarificationResumeKind
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
from apps.chatbi.orchestration.agent.tools.interaction import (
    prepare_semantic_clarification_args,
)
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
from apps.tool.tools.semantic_contracts import SemanticCompileFilter
from apps.trace import (
    AgentTraceRecorder,
    TraceNodeSpec,
    TraceNodeStatus,
    TraceNodeType,
)
from common.observability import MetricsRecorder


def _canonical_time_range(value: Any) -> Any:
    """把旧计划的简化日期结构转换为统一的绝对范围。"""

    if not isinstance(value, dict) or value.get("kind"):
        return value
    start = value.get("start")
    end_exclusive = value.get("end_exclusive") or value.get("end")
    if not start or not end_exclusive:
        return value
    return {
        **value,
        "kind": "absolute_range",
        "start": start,
        "end_exclusive": end_exclusive,
    }


def _task_time_range(task_spec: Any, time_dimension_id: int | None) -> dict[str, Any] | None:
    """从 QueryTask 的显式时间范围或已绑定时间筛选提取唯一时间条件。"""

    direct_range = getattr(task_spec, "time_range", None)
    if isinstance(direct_range, dict):
        return _canonical_time_range(direct_range)
    for raw_filter in getattr(task_spec, "filters", ()) or ():
        if not isinstance(raw_filter, dict):
            continue
        if raw_filter.get("asset_id") != time_dimension_id:
            continue
        value = raw_filter.get("value")
        if isinstance(value, dict) and (value.get("kind") or value.get("start")):
            return _canonical_time_range(value)
    return None


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
    planner_model_service: Any | None = None
    # 直接规划流水线也必须写入同一棵 Agent Trace 调用树。
    trace_recorder: AgentTraceRecorder | None = None


class PlanPipeline:
    """PLAN 的规则规划和多查询执行通道。"""

    def __init__(self, dependencies: PlanPipelineDependencies) -> None:
        self._registry = dependencies.registry
        self._result_processor = dependencies.result_processor
        self._finalization_service = dependencies.finalization_service
        self._lifecycle = dependencies.lifecycle
        self._events = PipelineEvents(dependencies.event_publisher)
        self._session = dependencies.session
        self._planner = AnalysisPlanner(
            max_query_tasks=dependencies.max_query_tasks,
            model_service=dependencies.planner_model_service,
        )
        self._max_query_tasks = dependencies.max_query_tasks
        self._compute_engine = dependencies.compute_engine
        self._compute_enabled = dependencies.compute_enabled
        self._answer_composer = dependencies.answer_composer
        self._metrics = dependencies.metrics
        self._trace_recorder = dependencies.trace_recorder
        self._plan_snapshot_counts: dict[int, int] = {}

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
        if self._is_ambiguous(state):
            yield from self._suspend_semantic_clarification(state, plan_id)
            return
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
            if "PLAN_QUERY_GROUP_LIMIT_EXCEEDED" in plan.validation.reason_codes:
                # 预算超限是内部计划复杂度错误，不能转换为用户信息不足澄清。
                raise PlanPipelineError(
                    "PLAN_QUERY_GROUP_LIMIT_EXCEEDED",
                    "查询组数量超过系统计划上限，请拆分为多个问题",
                )
            raise PlanPipelineError(
                "PLAN_VALIDATION_FAILED",
                ",".join(plan.validation.reason_codes),
            )
        query_tasks = [task for task in plan.tasks if isinstance(task, QueryTask)]
        if not query_tasks:
            raise PlanPipelineError("PLAN_QUERY_TASK_REQUIRED")
        scope = state.context.semantic_asset_scope
        if scope is not None and scope.semantic_enforcement == "STRICT":
            self._ensure_strict_query_plans_ready(scope, query_tasks)

        execution_records: dict[str, dict[str, Any]] = {}
        full_data_records: dict[str, list[dict[str, Any]]] = {}
        completed_tasks: dict[str, QueryTask] = {}
        for query_index, task in enumerate(query_tasks):
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
            compiled = self._compile_task(state, task, strict_query_index=query_index)
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
        answer_execution = {
            **primary_execution,
            "result_sets": {
                str(execution.get("result_set_id") or task_id): {
                    **execution,
                    "rows": full_data_records.get(
                        task_id,
                        execution.get("sample_rows") or [],
                    ),
                }
                for task_id, execution in execution_records.items()
            },
        }
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
                    execution=answer_execution,
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

    def _suspend_semantic_clarification(
        self,
        state: AgentRuntimeState,
        plan_id: str,
    ) -> Iterator[RenderEvent]:
        """语义绑定存在歧义时暂停 PLAN，等待用户确认后再生成计划。"""

        clarification = prepare_semantic_clarification_args(state.context.state)
        if clarification is None or not clarification.options:
            raise PlanPipelineError("PLAN_SEMANTIC_CLARIFICATION_OPTIONS_MISSING")
        if not state.chatbi_budget.record_clarification().allowed:
            raise PlanPipelineError("PLAN_CLARIFICATION_BUDGET_EXHAUSTED")
        scope = state.context.state.get("semantic_scope")
        retrieval_id = scope.get("retrieval_id") if isinstance(scope, dict) else None
        options = [item.model_dump(mode="json") for item in clarification.options]
        call_id = f"plan:{state.require_run_id()}:semantic_clarification"
        yield self._lifecycle.suspend(
            state,
            clarification.question,
            options,
            call_id,
            None,
            resume_kind=AgentClarificationResumeKind.AGENT_TOOL,
            resume_payload={
                "operation": "resolve_semantic_bindings",
                "retrieval_id": retrieval_id,
                "options": options,
                "plan_id": plan_id,
            },
        )

    @staticmethod
    def _is_ambiguous(state: AgentRuntimeState) -> bool:
        scope = state.context.semantic_asset_scope
        return bool(scope is not None and scope.decision_status.value == "ambiguous")

    def _compile_task(
        self,
        state: AgentRuntimeState,
        task: QueryTask,
        *,
        strict_query_index: int = 0,
    ) -> CompiledQuery:
        scope = state.context.semantic_asset_scope
        if scope is None:
            raise PlanPipelineError("PLAN_STRICT_MULTI_QUERY_NOT_READY")
        if scope.semantic_enforcement == "STRICT":
            query_plans = scope.query_plans or ((scope.query_plan,) if scope.query_plan else ())
            if not query_plans:
                raise PlanPipelineError("PLAN_STRICT_QUERY_PLAN_MISSING")
            task_spec = getattr(task, "spec", None)
            selected_plan = None
            if task_spec is not None:
                selected_plan = next(
                    (
                        candidate
                        for candidate in query_plans
                        if {
                            item.metric_id for item in candidate.metrics
                        }
                        == set(task_spec.metric_ids)
                        and {
                            item.physical_dimension_id for item in candidate.dimensions
                        }
                        == set(task_spec.dimension_ids)
                    ),
                    None,
                )
            if selected_plan is None and task_spec is None:
                if strict_query_index < 0 or strict_query_index >= len(query_plans):
                    raise PlanPipelineError("PLAN_STRICT_QUERY_PLAN_MISSING")
                selected_plan = query_plans[strict_query_index]
            if selected_plan is None and len(query_plans) == 1:
                selected_plan = query_plans[0]
            if selected_plan is None:
                raise PlanPipelineError("PLAN_STRICT_QUERY_PLAN_MISSING")
            task_time_range = (
                _task_time_range(task_spec, selected_plan.time_binding.dimension_id)
                if task_spec is not None
                else None
            )
            if task_time_range is not None:
                selected_plan = selected_plan.model_copy(
                    update={
                        "time_binding": selected_plan.time_binding.model_copy(
                            update={"time_range": task_time_range}
                        )
                    }
                )
            state.context.state["semantic_scope"] = scope.model_copy(
                update={"query_plan": selected_plan}
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
                plan_fingerprint=f"{selected_plan.fingerprint}:{task.id}",
                sql=str(payload["sql"]),
                tables=tuple(str(item) for item in payload.get("tables") or []),
            )
        if scope.query_plan is not None:
            raise PlanPipelineError("PLAN_QUERY_PLAN_UNEXPECTED")
        compile_plan = scope.compile_plan
        if compile_plan is None:
            raise PlanPipelineError("PLAN_COMPILE_PLAN_REQUIRED")
        temporal_plan = compile_plan.temporal_plan
        if task.spec.time_range is not None:
            task_time_range = _canonical_time_range(task.spec.time_range)
            time_dimension_id = task.spec.time_dimension_id
            if time_dimension_id is None:
                time_dimension_id = next(
                    (
                        item.asset_id
                        for item in temporal_plan.filters
                        if isinstance(item.value, dict) and item.value.get("kind")
                    ),
                    None,
                )
            if time_dimension_id is None:
                raise PlanPipelineError("PLAN_TIME_DIMENSION_REQUIRED")
            replaced = False
            temporal_filters = []
            for item in temporal_plan.filters:
                if item.asset_id == time_dimension_id:
                    temporal_filters.append(
                        item.model_copy(update={"value": task_time_range})
                    )
                    replaced = True
                else:
                    temporal_filters.append(item)
            if not replaced:
                temporal_filters.append(
                    SemanticCompileFilter(
                        asset_id=time_dimension_id,
                        value=task_time_range,
                    )
                )
            temporal_plan = temporal_plan.model_copy(
                update={"filters": tuple(temporal_filters)}
            )
        subset = compile_plan.model_copy(
            update={
                "metric_asset_ids": task.spec.metric_ids,
                "dimension_asset_ids": task.spec.dimension_ids,
                "limit": task.spec.limit or compile_plan.limit,
                "having": task.spec.having or compile_plan.having,
                "time_offset": task.spec.time_offset or compile_plan.time_offset,
                "temporal_plan": temporal_plan,
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

    @staticmethod
    def _ensure_strict_query_plans_ready(
        scope: Any,
        query_tasks: list[QueryTask] | None = None,
        *,
        query_task_count: int | None = None,
    ) -> None:
        """执行前统一检查每个 QueryTask 都有独立且已证明的严格计划。"""

        strict_query_plans = scope.query_plans or (
            (scope.query_plan,) if scope.query_plan is not None else ()
        )
        if not strict_query_plans:
            raise PlanPipelineError("PLAN_STRICT_QUERY_PLAN_MISSING")
        if query_tasks is None:
            if query_task_count is None:
                raise PlanPipelineError("PLAN_STRICT_QUERY_PLAN_MISSING")
            query_tasks = []
        task_signatures = {
            (
                tuple(sorted(task.spec.metric_ids)),
                tuple(sorted(task.spec.dimension_ids)),
            )
            for task in query_tasks
        }
        plan_signatures = {
            (
                tuple(sorted(item.metric_id for item in plan.metrics)),
                tuple(sorted(item.physical_dimension_id for item in plan.dimensions)),
            )
            for plan in strict_query_plans
        }
        if query_tasks and not task_signatures <= plan_signatures:
            raise PlanPipelineError("PLAN_STRICT_QUERY_PLAN_MISSING")
        if query_task_count is not None and len(strict_query_plans) < query_task_count and not task_signatures:
            raise PlanPipelineError("PLAN_STRICT_QUERY_PLAN_MISSING")
        if any(
            plan.validation_status.value != "PROVEN"
            for plan in strict_query_plans
        ):
            raise PlanPipelineError("PLAN_STRICT_QUERY_PLAN_NOT_PROVEN")

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
        trace_context = (
            self._trace_recorder.node(
                TraceNodeSpec(
                    run_id=state.require_run_id(),
                    node_key=(
                        f"pipeline:plan:{state.require_run_id()}:{name}:"
                        f"{state.context.state.get('result_node_id') or 'plan'}"
                    ),
                    node_type=_pipeline_trace_node_type(name),
                    name=_pipeline_trace_name(name),
                    display_name=_pipeline_trace_display_name(name),
                    metadata={"pipeline": "plan", "tool_name": name},
                ),
                input_data={"tool_name": name, "call_id": call_id},
                input_detail={
                    "args": args,
                    "semantic_retrieval_request": (
                        state.context.state.get("semantic_retrieval_request")
                        if name == "search_semantic_assets"
                        else None
                    ),
                },
            )
            if self._trace_recorder is not None
            else nullcontext(None)
        )
        with trace_context as trace_node:
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
            if trace_node is not None:
                trace_summary = {
                    "status": projection.result.status.value,
                    "error_code": projection.result.error_code,
                    "changed_keys": sorted(projection.state_patch),
                }
                if name == "search_semantic_assets":
                    # R1 门禁只需要稳定的分槽摘要；完整语义包仍按原规则进入 detail，
                    # 避免大结果截断时丢失“是否整句检索”的证据。
                    metadata = projection.result.metadata
                    if isinstance(metadata, dict):
                        if isinstance(metadata.get("semantic_retrieval_filters"), dict):
                            trace_summary["retrieval_filters"] = metadata[
                                "semantic_retrieval_filters"
                            ]
                        if isinstance(metadata.get("semantic_retrieval_request"), dict):
                            trace_summary["retrieval_request"] = metadata[
                                "semantic_retrieval_request"
                            ]
                trace_node.set_output(trace_summary)
                trace_node.set_output_detail(
                    {
                        "result": _pipeline_trace_result(projection.result),
                        "state_patch": projection.state_patch,
                    }
                )
                if projection.result.status is not ToolStatus.SUCCEEDED:
                    trace_node.set_status(TraceNodeStatus.FAILED)
            if projection.result.status is not ToolStatus.SUCCEEDED:
                raise PlanPipelineError(
                    projection.result.error_code or f"PLAN_{name.upper()}_FAILED",
                    projection.result.model_content,
                )
            return projection.result

    def _save_plan(self, state: AgentRuntimeState, plan: AnalysisPlan) -> None:
        state.context.state["analysis_plan"] = plan.model_dump(mode="json")
        PlanPipeline._persist_state(state)
        if self._trace_recorder is None:
            return
        run_id = state.require_run_id()
        snapshot_index = self._plan_snapshot_counts.get(run_id, 0) + 1
        self._plan_snapshot_counts[run_id] = snapshot_index
        with self._trace_recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"pipeline:plan:plan:{plan.id}:{snapshot_index}",
                node_type=TraceNodeType.PHASE,
                name="analysis_plan_snapshot",
                display_name="记录分析计划快照",
                metadata={"pipeline": "plan", "stage": "plan"},
            ),
            input_data={"plan_id": plan.id, "status": plan.validation.status.value},
        ) as plan_node:
            plan_node.set_output(
                {
                    "plan_id": plan.id,
                    "status": plan.validation.status.value,
                    "query_task_count": sum(
                        isinstance(task, QueryTask) for task in plan.tasks
                    ),
                    "compute_task_count": sum(
                        isinstance(task, ComputeTask) for task in plan.tasks
                    ),
                }
            )
            plan_node.set_output_detail({"analysis_plan": plan.model_dump(mode="json")})

    @staticmethod
    def _persist_state(state: AgentRuntimeState) -> None:
        agent_run_repository.update_run(
            state.context.session,
            state.run,
            derived_state=state.persistable_context(),
        )
        state.context.session.commit()


__all__ = ["PlanPipeline", "PlanPipelineDependencies", "PlanPipelineError"]


def _pipeline_trace_node_type(name: str) -> TraceNodeType:
    if name == "validate_sql":
        return TraceNodeType.VALIDATION
    if name == "execute_sql":
        return TraceNodeType.TOOL
    return TraceNodeType.PHASE


def _pipeline_trace_name(name: str) -> str:
    return {
        "search_semantic_assets": "semantic_retrieval",
        "compile_semantic_sql": "semantic_compilation",
        "validate_sql": "sql_validation",
        "execute_sql": "sql_execution",
        "get_dataset_schema": "schema_snapshot",
    }.get(name, f"pipeline_{name}")


def _pipeline_trace_display_name(name: str) -> str:
    return {
        "search_semantic_assets": "语义检索与绑定快照",
        "compile_semantic_sql": "语义编译快照",
        "validate_sql": "SQL 校验快照",
        "execute_sql": "SQL 执行快照",
        "get_dataset_schema": "数据集 Schema 快照",
    }.get(name, f"规划流水线工具：{name}")


def _pipeline_trace_result(result: ToolResult[Any]) -> dict[str, Any]:
    data = result.data
    return {
        "status": result.status.value,
        "model_content": result.model_content,
        "data": data.model_dump(mode="json") if data is not None else None,
        "metadata": result.metadata,
        "error_code": result.error_code,
        "error_category": (
            result.error_category.value if result.error_category is not None else None
        ),
        "details": result.details,
    }
