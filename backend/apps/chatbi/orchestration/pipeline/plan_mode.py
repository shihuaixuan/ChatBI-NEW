"""PLAN 模式：多 QueryTask 规划、校验与顺序执行。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from apps.chatbi.models.dto.analysis_plan import (
    AnalysisPlan,
    AnalysisPlanStatus,
    CompiledQuery,
    QueryTask,
)
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tool_results import ChatBIToolResultProcessor
from apps.chatbi.orchestration.pipeline.events import PipelineEvents
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.generation.agent_finalization import (
    AgentFinalizationInput,
    AgentFinalizationService,
)
from apps.chatbi.services.planning.analysis_planner import AnalysisPlanner
from apps.chatbi.services.planning.plan_validation import validate_analysis_plan
from apps.event import EventPublisher, RenderEvent
from apps.tool import ToolCall, ToolCallContext, ToolRegistry, ToolResult, ToolStatus


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

    def run(self, state: AgentRuntimeState) -> Iterator[RenderEvent]:
        run_id = state.require_run_id()
        understanding = state.context.state.get("question_understanding")
        if not isinstance(understanding, dict):
            raise PlanPipelineError("PLAN_QUESTION_UNDERSTANDING_REQUIRED")
        if state.context.semantic_asset_scope is None:
            self._call_tool(state, "search_semantic_assets", {})
        plan_id = f"plan-{run_id}"
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
        if any(task.type == "compute" for task in plan.tasks):
            raise PlanPipelineError(
                "PLAN_COMPUTE_ENGINE_REQUIRED",
                "跨结果集计算需要 P1-4 ComputeEngine。",
            )
        query_tasks = [task for task in plan.tasks if isinstance(task, QueryTask)]
        if not query_tasks:
            raise PlanPipelineError("PLAN_QUERY_TASK_REQUIRED")

        execution_records: list[dict[str, Any]] = []
        completed_tasks: list[QueryTask] = []
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
            completed_tasks.append(task.model_copy(update={"compiled": compiled}))
            self._save_plan(
                state,
                plan.model_copy(
                    update={
                        "tasks": (*completed_tasks, *query_tasks[len(completed_tasks) :])
                    }
                ),
            )
            state.context.state["result_node_id"] = task.id
            self._call_tool(state, "validate_sql", {"sql": compiled.sql})
            self._call_tool(state, "execute_sql", {"sql": compiled.sql})
            self._persist_state(state)
            execution = state.context.state.get("last_execution")
            execution_records.append(execution if isinstance(execution, dict) else {})
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

        final_plan = plan.model_copy(
            update={
                "tasks": tuple(completed_tasks),
                "validation": validate_analysis_plan(
                    plan.model_copy(update={"tasks": tuple(completed_tasks)}),
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

        primary_execution = execution_records[0]
        primary_rows = primary_execution.get("sample_rows") or []
        state.context.state["last_execution"] = primary_execution
        state.context.state["full_data"] = primary_rows
        intent = understanding.get("intent")
        final = self._finalization_service.generate(
            AgentFinalizationInput(
                question=str(
                    state.context.state.get("question") or state.record.question or ""
                ),
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
            full_data=primary_rows,
            execution=primary_execution,
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
