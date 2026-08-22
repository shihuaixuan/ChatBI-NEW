"""PLAN 模式：多 QueryTask 规划、校验与顺序执行。"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from dataclasses import dataclass
from typing import Any

from apps.chatbi.models.dto.execution_requirement import (
    AnalysisExecutionSpec,
    ExecutionRequirement,
    execution_requirement_from_state,
)
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.services.computation import ComputeEngine
from apps.chatbi.services.execution.analysis_execution import (
    AnalysisExecutionDependencies,
    AnalysisExecutionService,
    PlanExecutionOutcome,
    PlanPipelineError,
)
from apps.chatbi.services.execution.query_task_executor import QueryTaskExecutor
from apps.chatbi.services.generation.agent_finalization import (
    AgentFinalizationInput,
    AgentFinalizationService,
)
from apps.chatbi.services.generation.answer_composer import (
    AnswerComposer,
    AnswerComposerInput,
)
from apps.event import EventPublisher, RenderEvent
from apps.tool import ToolRegistry
from apps.trace import AgentTraceRecorder
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


def _task_time_range(
    task_spec: Any, time_dimension_id: int | None
) -> dict[str, Any] | None:
    """从 QueryTask 的显式时间范围或已绑定时间筛选提取唯一时间条件。"""

    direct_range = getattr(task_spec, "time_range", None)
    if isinstance(direct_range, dict):
        canonical = _canonical_time_range(direct_range)
        return canonical if isinstance(canonical, dict) else None
    for raw_filter in getattr(task_spec, "filters", ()) or ():
        if not isinstance(raw_filter, dict):
            continue
        if raw_filter.get("asset_id") != time_dimension_id:
            continue
        value = raw_filter.get("value")
        if isinstance(value, dict) and (value.get("kind") or value.get("start")):
            canonical = _canonical_time_range(value)
            return canonical if isinstance(canonical, dict) else None
    return None


def _time_ranges_equal(left: Any, right: Any) -> bool:
    """比较规范化后的时间范围，避免同口径的 current/previous 选错计划。"""

    if left is None or right is None:
        return left is None and right is None
    return bool(_canonical_time_range(left) == _canonical_time_range(right))


@dataclass(frozen=True, slots=True)
class PlanPipelineDependencies:
    """Plan 根入口依赖；回答依赖不会传入共享执行服务。"""

    registry: ToolRegistry
    result_processor: Any
    finalization_service: AgentFinalizationService
    lifecycle: AgentLifecycle
    event_publisher: EventPublisher
    session: Any
    query_task_executor: QueryTaskExecutor
    max_query_tasks: int = 5
    query_concurrency: int = 4
    query_timeout_seconds: float = 60.0
    compute_engine: ComputeEngine | None = None
    compute_enabled: bool = True
    answer_composer: AnswerComposer | None = None
    metrics: MetricsRecorder | None = None
    trace_recorder: AgentTraceRecorder | None = None
    semantic_schema_provider: Any | None = None


class PlanPipeline:
    """PLAN 模式的回答生成和生命周期编排。"""

    def __init__(self, dependencies: PlanPipelineDependencies) -> None:
        """组装根入口与共享执行服务；回答和生命周期仍由根入口负责。"""

        self._execution_service = AnalysisExecutionService(
            AnalysisExecutionDependencies(
                registry=dependencies.registry,
                result_processor=dependencies.result_processor,
                lifecycle=dependencies.lifecycle,
                event_publisher=dependencies.event_publisher,
                session=dependencies.session,
                query_task_executor=dependencies.query_task_executor,
                max_query_tasks=dependencies.max_query_tasks,
                query_concurrency=dependencies.query_concurrency,
                query_timeout_seconds=dependencies.query_timeout_seconds,
                compute_engine=dependencies.compute_engine,
                compute_enabled=dependencies.compute_enabled,
                trace_recorder=dependencies.trace_recorder,
                semantic_schema_provider=dependencies.semantic_schema_provider,
            )
        )
        self._finalization_service = dependencies.finalization_service
        self._lifecycle = dependencies.lifecycle
        self._answer_composer = dependencies.answer_composer
        self._metrics = dependencies.metrics

    @property
    def max_query_tasks(self) -> int:
        """兼容 legacy Research 对计划上限的读取。"""

        return self._execution_service.max_query_tasks

    def execute(
        self,
        state: AgentRuntimeState,
        spec: AnalysisExecutionSpec,
        *,
        plan_id: str,
    ) -> Generator[RenderEvent, None, PlanExecutionOutcome | None]:
        """把无根路由的执行规格转发给共享服务。"""

        return (yield from self._execution_service.execute(
            state,
            spec,
            plan_id=plan_id,
        ))

    def execute_requirement(
        self,
        state: AgentRuntimeState,
        execution_requirement: ExecutionRequirement,
        *,
        plan_id: str,
    ) -> Generator[RenderEvent, None, PlanExecutionOutcome | None]:
        """兼容 Plan 和 legacy Research 的根需求入口。"""

        return (yield from self._execution_service.execute_requirement(
            state,
            execution_requirement,
            plan_id=plan_id,
        ))

    _ensure_strict_query_plans_ready = staticmethod(
        AnalysisExecutionService._ensure_strict_query_plans_ready
    )

    @staticmethod
    def _load_execution_requirement(state: AgentRuntimeState) -> ExecutionRequirement:
        """读取路由阶段产物；根入口不重新绑定语义资产。"""

        try:
            return execution_requirement_from_state(state.context.state)
        except ValueError as exc:
            raise PlanPipelineError(str(exc)) from exc

    def run(self, state: AgentRuntimeState) -> Iterator[RenderEvent]:
        run_id = state.require_run_id()
        if self._metrics is not None:
            self._metrics.record_run(mode="plan", status="started")
        understanding = state.context.state.get("question_understanding")
        if not isinstance(understanding, dict):
            understanding = {}
        execution_requirement = self._load_execution_requirement(state)
        plan_id = f"plan-{run_id}"
        outcome = yield from self.execute_requirement(
            state,
            execution_requirement,
            plan_id=plan_id,
        )
        if outcome is None:
            return
        proven_plan = outcome.plan
        execution_records = outcome.execution_records
        full_data_records = outcome.full_data_records
        primary_execution = outcome.primary_execution
        primary_rows = primary_execution.get("sample_rows") or []
        primary_full_data = outcome.primary_rows
        answer_execution = {
            **primary_execution,
            "result_contract": proven_plan.presentation.model_dump(mode="json"),
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
            plan_payload = state.context.state.get("analysis_plan")
            semantic_payload = state.context.state.get("semantic_scope")
            composed = self._answer_composer.compose(
                AnswerComposerInput(
                    question=question,
                    intent=intent if isinstance(intent, dict) else {},
                    execution=answer_execution,
                    rows=primary_full_data,
                    plan=dict(plan_payload) if isinstance(plan_payload, dict) else {},
                    semantic_context=(
                        dict(semantic_payload)
                        if isinstance(semantic_payload, dict)
                        else {}
                    ),
                    mode="plan",
                )
            )
            answer = composed.answer
            chart = composed.chart
            claims = list(getattr(composed, "claims", []) or [])
            caliber_card = dict(getattr(composed, "caliber_card", {}) or {})
            chart_spec = dict(getattr(composed, "chart_spec", {}) or {})
        else:
            generated = self._finalization_service.generate(
                AgentFinalizationInput(
                    question=question,
                    intent=intent if isinstance(intent, dict) else {},
                    execution=primary_execution,
                    rows=primary_rows,
                )
            )
            answer = generated.answer
            chart = generated.chart
            claims = list(getattr(generated, "claims", []) or [])
            caliber_card = dict(getattr(generated, "caliber_card", {}) or {})
            chart_spec = dict(getattr(generated, "chart_spec", {}) or {})
        yield from self._lifecycle.finish(
            state,
            answer=answer,
            chart=chart,
            sql=primary_execution.get("sql"),
            full_data=primary_full_data,
            execution=primary_execution,
            claims=claims,
            caliber_card=caliber_card,
            chart_spec=chart_spec,
        )


__all__ = [
    "AnalysisExecutionService",
    "PlanExecutionOutcome",
    "PlanPipeline",
    "PlanPipelineDependencies",
    "PlanPipelineError",
]
