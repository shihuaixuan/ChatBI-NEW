"""PLAN 模式：多 QueryTask 规划、校验与顺序执行。"""

from __future__ import annotations

import time
from collections.abc import Generator, Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, cast

from apps.chatbi.models import AgentClarificationResumeKind
from apps.chatbi.models.dto.analysis_evidence import AnalysisEvidenceDependency
from apps.chatbi.models.dto.analysis_plan import (
    AnalysisPlan,
    AnalysisPlanStatus,
    CompiledQuery,
    ComputeTask,
    QueryTask,
    ResultSetKind,
    ResultSetRef,
)
from apps.chatbi.models.dto.execution_requirement import (
    AnalysisExecutionSpec,
    ExecutionRequirement,
    execution_requirement_from_state,
)
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tools.interaction import (
    prepare_semantic_clarification_args,
)
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.computation import (
    ComputeEngine,
    ComputeEngineError,
    ComputeExecution,
)
from apps.chatbi.services.evidence import (
    EvidenceRegistry,
    build_analysis_evidence,
    build_analysis_version_snapshot,
)
from apps.chatbi.services.execution.query_task_executor import (
    QueryTaskExecutionRequest,
    QueryTaskExecutionResult,
    QueryTaskExecutionStatus,
    QueryTaskExecutor,
)
from apps.chatbi.services.planning.analysis_planner import AnalysisPlanner
from apps.chatbi.services.planning.dag_scheduler import (
    AnalysisTaskExecutionStatus,
    build_execution_batches,
    task_dependencies,
)
from apps.chatbi.services.planning.plan_validation import validate_analysis_plan
from apps.chatbi.services.planning.semantic_query_preparation import (
    prepare_strict_query_scope,
)
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


class _AnalysisExecutionEvents:
    """执行服务使用的最小事件适配器，避免依赖编排包的初始化入口。"""

    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

    def plan_created(self, run_id: int, payload: dict[str, Any]) -> RenderEvent:
        return self._publisher.publish(run_id, "plan-created", payload)

    def plan_updated(self, run_id: int, payload: dict[str, Any]) -> RenderEvent:
        return self._publisher.publish(run_id, "plan-updated", payload)

    def task_started(self, run_id: int, payload: dict[str, Any]) -> RenderEvent:
        return self._publisher.publish(run_id, "task-started", payload)

    def task_finished(self, run_id: int, payload: dict[str, Any]) -> RenderEvent:
        return self._publisher.publish(run_id, "task-finished", payload)

    def compute_finished(self, run_id: int, payload: dict[str, Any]) -> RenderEvent:
        return self._publisher.publish(run_id, "compute-finished", payload)


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


class PlanPipelineError(RuntimeError):
    """PLAN 模式无法安全进入下一阶段。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True, slots=True)
class AnalysisExecutionDependencies:
    """共享分析执行只依赖计划、证明、查询、计算和结果存储能力。"""

    registry: ToolRegistry
    result_processor: Any
    lifecycle: AgentLifecycle
    event_publisher: EventPublisher
    session: Any
    query_task_executor: QueryTaskExecutor
    max_query_tasks: int = 5
    query_concurrency: int = 4
    query_timeout_seconds: float = 60.0
    compute_engine: ComputeEngine | None = None
    compute_enabled: bool = True
    # 直接规划流水线也必须写入同一棵 Agent Trace 调用树。
    trace_recorder: AgentTraceRecorder | None = None
    semantic_schema_provider: Any | None = None


@dataclass(frozen=True, slots=True)
class PlanExecutionOutcome:
    """完成证明和执行后的计划结果，供 Plan 回答与 Research 证据投影复用。"""

    plan: AnalysisPlan
    execution_records: dict[str, dict[str, Any]]
    full_data_records: dict[str, list[dict[str, Any]]]
    primary_execution: dict[str, Any]
    primary_rows: list[dict[str, Any]]


class AnalysisExecutionService:
    """受治理的分析计划生成、证明、执行和结果存储服务。"""

    def __init__(self, dependencies: AnalysisExecutionDependencies) -> None:
        if dependencies.query_concurrency <= 0:
            raise ValueError("PLAN_QUERY_CONCURRENCY_INVALID")
        if dependencies.query_timeout_seconds <= 0:
            raise ValueError("PLAN_QUERY_TIMEOUT_INVALID")
        self._registry = dependencies.registry
        self._result_processor = dependencies.result_processor
        self._lifecycle = dependencies.lifecycle
        self._events = _AnalysisExecutionEvents(dependencies.event_publisher)
        self._session = dependencies.session
        self._planner = AnalysisPlanner(max_query_tasks=dependencies.max_query_tasks)
        self._max_query_tasks = dependencies.max_query_tasks
        self._query_task_executor = dependencies.query_task_executor
        self._query_concurrency = dependencies.query_concurrency
        self._query_timeout_seconds = dependencies.query_timeout_seconds
        self._compute_engine = dependencies.compute_engine
        self._compute_enabled = dependencies.compute_enabled
        self._trace_recorder = dependencies.trace_recorder
        self._semantic_schema_provider = dependencies.semantic_schema_provider
        self._plan_snapshot_counts: dict[int, int] = {}

    @property
    def max_query_tasks(self) -> int:
        """单个 Plan 需求允许的最大查询组数量（与校验器上限一致）。"""

        return self._max_query_tasks

    def execute(
        self,
        state: AgentRuntimeState,
        spec: AnalysisExecutionSpec,
        *,
        plan_id: str,
    ) -> Generator[RenderEvent, None, PlanExecutionOutcome | None]:
        """生成、证明并执行一组分析需求，但不生成最终回答。"""

        run_id = state.require_run_id()
        dataset_id = spec.runtime.get("dataset_id")
        if not isinstance(dataset_id, int) or isinstance(dataset_id, bool):
            dataset_id = state.context.dataset_id or 0
        try:
            plan = self._planner.plan_spec(
                plan_id=plan_id,
                spec=spec,
                dataset_id=dataset_id,
            )
        except ValueError as exc:
            raise PlanPipelineError(str(exc)) from exc
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
            try:
                requirement_by_id = {
                    item.id: item for item in spec.query_requirements
                }
                query_requirements = tuple(
                    requirement_by_id[task.source_requirement_id]
                    for task in query_tasks
                    if task.source_requirement_id in requirement_by_id
                )
                if len(query_requirements) != len(query_tasks):
                    raise ValueError("PLAN_STRICT_SOURCE_REQUIREMENT_NOT_MATCHED")
                dataset_id_value = spec.runtime.get("dataset_id")
                if not isinstance(dataset_id_value, int) or isinstance(
                    dataset_id_value, bool
                ):
                    dataset_id_value = state.context.dataset_id or 0
                scope = prepare_strict_query_scope(
                    scope,
                    query_requirements,
                    schema_provider=self._semantic_schema_provider,
                    schema_snapshot=spec.asset_snapshot.get(
                        "dataset_schema"
                    ),
                    workspace_id=state.context.workspace_id,
                    dataset_id=dataset_id_value,
                )
                state.context.state["semantic_scope"] = scope.model_dump(mode="json")
            except ValueError as exc:
                raise PlanPipelineError(str(exc)) from exc
            self._ensure_strict_query_plans_ready(scope, query_tasks)

        # 完整子计划必须先全部编译和校验，达到 PROVEN 后才允许执行任何查询。
        completed_tasks: dict[str, QueryTask] = {}
        for query_index, task in enumerate(query_tasks):
            compiled = self._compile_task(state, task, strict_query_index=query_index)
            self._call_tool(state, "validate_sql", {"sql": compiled.sql})
            completed_tasks[task.id] = task.model_copy(update={"compiled": compiled})
        proven_plan = plan.model_copy(
            update={
                "tasks": tuple(
                    completed_tasks.get(task.id, task) for task in plan.tasks
                )
            }
        )
        proven_validation = validate_analysis_plan(
            proven_plan,
            max_query_tasks=self._max_query_tasks,
            require_proven=True,
        )
        if proven_validation.status is not AnalysisPlanStatus.PROVEN:
            raise PlanPipelineError(
                "PLAN_PROOF_FAILED",
                ",".join(proven_validation.reason_codes),
            )
        proven_plan = proven_plan.model_copy(update={"validation": proven_validation})
        self._save_plan(state, proven_plan)
        self._session.commit()
        yield self._events.plan_updated(
            run_id,
            {
                "record_id": state.record.id,
                "run_id": run_id,
                "plan_id": plan_id,
                "status": AnalysisPlanStatus.PROVEN.value,
            },
        )
        execution_records: dict[str, dict[str, Any]] = {}
        full_data_records: dict[str, list[dict[str, Any]]] = {}
        cancelled = yield from self._execute_plan_batches(
            state,
            proven_plan,
            execution_records,
            full_data_records,
        )
        if cancelled:
            return None
        primary_result_id = proven_plan.presentation.primary_result
        primary_execution = execution_records.get(primary_result_id)
        if primary_execution is None:
            raise PlanPipelineError("PLAN_PRIMARY_RESULT_MISSING")
        primary_rows = full_data_records.get(
            primary_result_id,
            primary_execution.get("sample_rows") or [],
        )
        return PlanExecutionOutcome(
            plan=proven_plan,
            execution_records=execution_records,
            full_data_records=full_data_records,
            primary_execution=primary_execution,
            primary_rows=primary_rows,
        )

    def execute_requirement(
        self,
        state: AgentRuntimeState,
        execution_requirement: ExecutionRequirement,
        *,
        plan_id: str,
    ) -> Generator[RenderEvent, None, PlanExecutionOutcome | None]:
        """兼容 Plan 根入口，并把根路由投影为共享执行规格。"""

        try:
            spec = AnalysisExecutionSpec.from_requirement(execution_requirement)
        except ValueError as exc:
            raise PlanPipelineError(str(exc)) from exc
        return (yield from self.execute(state, spec, plan_id=plan_id))

    def _execute_plan_batches(
        self,
        state: AgentRuntimeState,
        plan: AnalysisPlan,
        execution_records: dict[str, dict[str, Any]],
        full_data_records: dict[str, list[dict[str, Any]]],
    ) -> Generator[RenderEvent, None, bool]:
        """按拓扑批次并行执行，所有共享状态只在主线程更新。"""

        try:
            batches = build_execution_batches(plan)
        except ValueError as exc:
            raise PlanPipelineError(str(exc)) from exc
        tasks = {task.id: task for task in plan.tasks}
        dependencies = task_dependencies(plan)
        task_states = {
            task.id: {
                "status": AnalysisTaskExecutionStatus.PENDING.value,
                "attempt": 0,
                "error_code": None,
            }
            for task in plan.tasks
        }
        state.context.state["plan_execution_batches"] = [list(batch) for batch in batches]
        state.context.state["plan_task_states"] = task_states
        self._persist_state(state)

        terminal_dependency_states = {
            AnalysisTaskExecutionStatus.FAILED.value,
            AnalysisTaskExecutionStatus.SKIPPED_DEPENDENCY.value,
            AnalysisTaskExecutionStatus.CANCELLED.value,
        }
        for batch_index, batch in enumerate(batches, start=1):
            state.context.state["current_plan_batch"] = batch_index
            if state.cancellation.is_cancelled():
                for task_id, task_state in task_states.items():
                    if task_state["status"] not in {
                        AnalysisTaskExecutionStatus.SUCCEEDED.value,
                        *terminal_dependency_states,
                    }:
                        self._set_task_state(
                            task_states,
                            task_id,
                            AnalysisTaskExecutionStatus.CANCELLED,
                            error_code="query_cancelled",
                        )
                self._persist_state(state)
                yield from self._lifecycle.cancel(
                    state,
                    "用户在计划批次执行前请求取消运行",
                )
                return True

            executable_ids: list[str] = []
            for task_id in batch:
                failed_dependencies = [
                    dependency_id
                    for dependency_id in dependencies[task_id]
                    if task_states[dependency_id]["status"]
                    in terminal_dependency_states
                ]
                if failed_dependencies:
                    self._set_task_state(
                        task_states,
                        task_id,
                        AnalysisTaskExecutionStatus.SKIPPED_DEPENDENCY,
                        error_code="PLAN_DEPENDENCY_FAILED",
                        failed_dependencies=failed_dependencies,
                    )
                    yield self._events.task_finished(
                        state.require_run_id(),
                        self._task_event_payload(
                            state,
                            plan.id,
                            task_id,
                            AnalysisTaskExecutionStatus.SKIPPED_DEPENDENCY,
                            batch_index=batch_index,
                            error_code="PLAN_DEPENDENCY_FAILED",
                        ),
                    )
                    continue
                self._set_task_state(
                    task_states,
                    task_id,
                    AnalysisTaskExecutionStatus.READY,
                )
                executable_ids.append(task_id)

            for task_id in executable_ids:
                self._set_task_state(
                    task_states,
                    task_id,
                    AnalysisTaskExecutionStatus.RUNNING,
                    increment_attempt=True,
                )
                yield self._events.task_started(
                    state.require_run_id(),
                    self._task_event_payload(
                        state,
                        plan.id,
                        task_id,
                        AnalysisTaskExecutionStatus.RUNNING,
                        batch_index=batch_index,
                        attempt=task_states[task_id]["attempt"],
                    ),
                )
            self._persist_state(state)

            query_tasks: list[QueryTask] = []
            compute_tasks: list[ComputeTask] = []
            for task_id in executable_ids:
                executable_task = tasks[task_id]
                if isinstance(executable_task, QueryTask):
                    query_tasks.append(executable_task)
                else:
                    compute_tasks.append(executable_task)
            query_results = self._run_query_batch(
                state,
                query_tasks,
                task_states,
                plan_id=plan.id,
            )
            compute_results = self._run_compute_batch(state, plan.id, compute_tasks)

            for task_id in executable_ids:
                task = tasks[task_id]
                if isinstance(task, QueryTask):
                    result = query_results[task_id]
                    if result.status is QueryTaskExecutionStatus.SUCCEEDED:
                        if result.data is None:
                            raise PlanPipelineError("PLAN_QUERY_RESULT_MISSING")
                        execution, rows = self._register_query_result(
                            state,
                            plan.id,
                            task,
                            result,
                        )
                    else:
                        status = (
                            AnalysisTaskExecutionStatus.CANCELLED
                            if result.status is QueryTaskExecutionStatus.CANCELLED
                            else AnalysisTaskExecutionStatus.FAILED
                        )
                        self._set_task_state(
                            task_states,
                            task_id,
                            status,
                            error_code=result.error_code,
                            message=result.message,
                        )
                        yield self._events.task_finished(
                            state.require_run_id(),
                            self._task_event_payload(
                                state,
                                plan.id,
                                task_id,
                                status,
                                batch_index=batch_index,
                                error_code=result.error_code,
                            ),
                        )
                        continue
                else:
                    computed = compute_results[task_id]
                    if isinstance(computed, ComputeEngineError):
                        compute_status = (
                            AnalysisTaskExecutionStatus.CANCELLED
                            if computed.code in {"COMPUTE_CANCELLED", "COMPUTE_TIMEOUT"}
                            else AnalysisTaskExecutionStatus.FAILED
                        )
                        self._set_task_state(
                            task_states,
                            task_id,
                            compute_status,
                            error_code=computed.code,
                            message=str(computed),
                        )
                        yield self._events.task_finished(
                            state.require_run_id(),
                            self._task_event_payload(
                                state,
                                plan.id,
                                task_id,
                                compute_status,
                                batch_index=batch_index,
                                error_code=computed.code,
                            ),
                        )
                        continue
                    attempt_value = task_states[task_id]["attempt"]
                    if not isinstance(attempt_value, int):
                        raise PlanPipelineError("PLAN_TASK_ATTEMPT_INVALID")
                    execution, rows = self._register_compute_result(
                        state,
                        plan.id,
                        task,
                        computed,
                        attempt=attempt_value,
                    )
                    yield self._events.compute_finished(
                        state.require_run_id(),
                        self._task_event_payload(
                            state,
                            plan.id,
                            task_id,
                            AnalysisTaskExecutionStatus.SUCCEEDED,
                            batch_index=batch_index,
                            result_set_id=execution.get("result_set_id"),
                        ),
                    )

                execution_records[task_id] = execution
                full_data_records[task_id] = rows
                self._set_task_state(
                    task_states,
                    task_id,
                    AnalysisTaskExecutionStatus.SUCCEEDED,
                    result_set_id=execution.get("result_set_id"),
                )
                yield self._events.task_finished(
                    state.require_run_id(),
                    self._task_event_payload(
                        state,
                        plan.id,
                        task_id,
                        AnalysisTaskExecutionStatus.SUCCEEDED,
                        batch_index=batch_index,
                        result_set_id=execution.get("result_set_id"),
                    ),
                )
            self._persist_state(state)

            if state.cancellation.is_cancelled():
                for task_id, task_state in task_states.items():
                    if task_state["status"] in {
                        AnalysisTaskExecutionStatus.PENDING.value,
                        AnalysisTaskExecutionStatus.READY.value,
                        AnalysisTaskExecutionStatus.RUNNING.value,
                    }:
                        self._set_task_state(
                            task_states,
                            task_id,
                            AnalysisTaskExecutionStatus.CANCELLED,
                            error_code="query_cancelled",
                        )
                self._persist_state(state)
                yield from self._lifecycle.cancel(
                    state,
                    "用户在计划批次执行期间请求取消运行",
                )
                return True

        primary_state = task_states[plan.presentation.primary_result]
        if primary_state["status"] != AnalysisTaskExecutionStatus.SUCCEEDED.value:
            raise PlanPipelineError(
                str(primary_state.get("error_code") or "PLAN_PRIMARY_RESULT_FAILED")
            )
        return False

    def _run_query_batch(
        self,
        state: AgentRuntimeState,
        tasks: list[QueryTask],
        task_states: dict[str, dict[str, Any]],
        *,
        plan_id: str | None = None,
    ) -> dict[str, QueryTaskExecutionResult]:
        if not tasks:
            return {}
        context = state.context
        if not all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in (context.datasource_id, context.oid, context.user_id)
        ):
            raise PlanPipelineError("PLAN_QUERY_OWNERSHIP_REQUIRED")
        datasource_id = context.datasource_id
        workspace_id = context.oid
        user_id = context.user_id
        if not isinstance(datasource_id, int) or isinstance(datasource_id, bool):
            raise PlanPipelineError("PLAN_QUERY_OWNERSHIP_REQUIRED")
        if not isinstance(workspace_id, int) or isinstance(workspace_id, bool):
            raise PlanPipelineError("PLAN_QUERY_OWNERSHIP_REQUIRED")
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            raise PlanPipelineError("PLAN_QUERY_OWNERSHIP_REQUIRED")
        effective_plan_id = plan_id or "unknown-plan"
        deadline = time.monotonic() + min(
            self._query_timeout_seconds,
            state.budget.remaining_seconds(),
        )
        requests: list[QueryTaskExecutionRequest] = []
        for task in tasks:
            compiled = task.compiled
            if compiled is None:
                raise PlanPipelineError("PLAN_QUERY_TASK_NOT_PROVEN")
            requests.append(
                QueryTaskExecutionRequest(
                    task_id=task.id,
                    attempt=int(task_states[task.id]["attempt"]),
                    sql=compiled.sql,
                    datasource_id=datasource_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    selected_tables=compiled.tables,
                    deadline_monotonic=deadline,
                    cancellation=state.cancellation,
                    idempotency_key=(
                        f"plan-query:{self._required_execution_id(state)}:"
                        f"{effective_plan_id}:{task.id}:"
                        f"{int(task_states[task.id]['attempt'])}"
                    ),
                )
            )
        results: dict[str, QueryTaskExecutionResult] = {}
        pool = ThreadPoolExecutor(
            max_workers=min(self._query_concurrency, len(requests)),
            thread_name_prefix="chatbi-plan-query",
        )
        future_requests = {
            pool.submit(self._query_task_executor.execute, request): request
            for request in requests
        }
        pending = set(future_requests)
        try:
            while pending:
                if state.cancellation.is_cancelled():
                    break
                remaining = max(deadline - time.monotonic(), 0.0)
                if remaining <= 0:
                    break
                completed, pending = wait(
                    pending,
                    timeout=min(remaining, 0.05),
                    return_when=FIRST_COMPLETED,
                )
                for future in completed:
                    request = future_requests[future]
                    # QueryTaskExecutor 已将可预期的数据源失败编码为结果；未知程序异常
                    # 必须继续抛出，不能伪装成可重试的查询失败。
                    results[request.task_id] = future.result()
            # 取消或超时与轮询交错时，先收取已经完成的任务，避免误报为取消。
            if pending:
                completed, pending = wait(
                    pending,
                    timeout=0,
                    return_when=FIRST_COMPLETED,
                )
                for future in completed:
                    request = future_requests[future]
                    results[request.task_id] = future.result()
            if pending:
                cancelled = state.cancellation.is_cancelled()
                for future in pending:
                    future.cancel()
                    request = future_requests[future]
                    results[request.task_id] = QueryTaskExecutionResult(
                        task_id=request.task_id,
                        attempt=request.attempt,
                        status=QueryTaskExecutionStatus.CANCELLED,
                        error_code=("query_cancelled" if cancelled else "query_timeout"),
                        message=(
                            "计划批次收到取消请求，结果未合并"
                            if cancelled
                            else "计划查询超过批次截止时间，结果未合并"
                        ),
                    )
        finally:
            # 运行中的数据源调用通过独立截止时间自行退出；主线程不再等待它们。
            pool.shutdown(wait=not pending, cancel_futures=True)
        return results

    def _run_compute_batch(
        self,
        state: AgentRuntimeState,
        plan_id: str,
        tasks: list[ComputeTask],
    ) -> dict[str, ComputeExecution | ComputeEngineError]:
        if not tasks:
            return {}
        if not self._compute_enabled:
            raise PlanPipelineError("PLAN_COMPUTE_DISABLED")
        if self._compute_engine is None:
            raise PlanPipelineError("PLAN_COMPUTE_ENGINE_REQUIRED")
        deadline = time.monotonic() + min(
            self._query_timeout_seconds,
            state.budget.remaining_seconds(),
        )
        now = time.monotonic()
        if state.cancellation.is_cancelled() or now >= deadline:
            error_code = (
                "COMPUTE_TIMEOUT"
                if now >= deadline
                else "COMPUTE_CANCELLED"
            )
            return {task.id: ComputeEngineError(error_code) for task in tasks}
        prepared = {
            task.id: self._load_compute_inputs(state, plan_id, task) for task in tasks
        }
        results: dict[str, ComputeExecution | ComputeEngineError] = {}
        pool = ThreadPoolExecutor(
            max_workers=min(self._query_concurrency, len(tasks)),
            thread_name_prefix="chatbi-plan-compute",
        )
        future_tasks = {
            pool.submit(self._compute_engine.execute, task, prepared[task.id]): task
            for task in tasks
        }
        pending = set(future_tasks)
        try:
            while pending:
                if state.cancellation.is_cancelled():
                    break
                remaining = max(deadline - time.monotonic(), 0.0)
                if remaining <= 0:
                    break
                completed, pending = wait(
                    pending,
                    timeout=min(remaining, 0.05),
                    return_when=FIRST_COMPLETED,
                )
                for future in completed:
                    task = future_tasks[future]
                    # 计算引擎只声明 ComputeEngineError 为业务失败；未知异常不能吞掉。
                    try:
                        results[task.id] = future.result()
                    except ComputeEngineError as exc:
                        results[task.id] = exc
            # 取消或超时与轮询交错时，先收取已经完成的任务，避免误报为取消。
            if pending:
                completed, pending = wait(
                    pending,
                    timeout=0,
                    return_when=FIRST_COMPLETED,
                )
                for future in completed:
                    task = future_tasks[future]
                    try:
                        results[task.id] = future.result()
                    except ComputeEngineError as exc:
                        results[task.id] = exc
            if pending:
                error_code = (
                    "COMPUTE_CANCELLED"
                    if state.cancellation.is_cancelled()
                    else "COMPUTE_TIMEOUT"
                )
                for future in pending:
                    future.cancel()
                    results[future_tasks[future].id] = ComputeEngineError(error_code)
        finally:
            pool.shutdown(wait=not pending, cancel_futures=True)
        return results

    def _load_compute_inputs(
        self,
        state: AgentRuntimeState,
        plan_id: str,
        task: ComputeTask,
    ) -> dict[str, Any]:
        result_store = state.context.result_store
        result_sets = state.context.state.get("result_sets")
        if result_store is None or not isinstance(result_sets, dict):
            raise PlanPipelineError("PLAN_INPUT_RESULT_SETS_MISSING")
        snapshots = {}
        for input_id in task.inputs:
            result_set_id = self._result_set_id_for_node(
                result_sets,
                plan_id,
                input_id,
            )
            payload = result_sets.get(result_set_id) if result_set_id else None
            if not isinstance(payload, dict):
                raise PlanPipelineError("PLAN_INPUT_RESULT_SET_MISSING")
            snapshots[input_id] = result_store.read(
                ResultSetRef.model_validate(payload),
                execution_id=self._required_execution_id(state),
                execution_type=ChatRecordExecutionType.AGENT,
                chat_id=self._required_chat_id(state),
                record_id=self._required_record_id(state),
            )
        return snapshots

    def _register_query_result(
        self,
        state: AgentRuntimeState,
        plan_id: str,
        task: QueryTask,
        result: QueryTaskExecutionResult,
        *,
        iteration: int = 0,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if result.data is None or state.context.result_store is None:
            raise PlanPipelineError("PLAN_QUERY_RESULT_STORE_REQUIRED")
        iteration = iteration or int(state.context.state.get("current_plan_batch") or 0)
        data = result.data
        rows = [dict(row) for row in data.full_data]
        result_ref = state.context.result_store.register(
            execution_id=self._required_execution_id(state),
            execution_type=ChatRecordExecutionType.AGENT,
            chat_id=self._required_chat_id(state),
            record_id=self._required_record_id(state),
            plan_id=plan_id,
            node_id=task.id,
            kind=ResultSetKind.QUERY,
            fields=list(data.fields),
            rows=rows,
            row_count=data.row_count,
            attempt=result.attempt,
            source_sql=data.sql,
            semantic_refs=self._semantic_refs(state),
            idempotency_key=(
                f"plan-query:{self._required_execution_id(state)}:"
                f"{plan_id}:{task.id}:{result.attempt}"
            ),
        )
        self._merge_result_ref(state, result_ref)
        self._register_query_evidence(
            state,
            plan_id,
            task,
            result_ref=result_ref,
            fields=list(data.fields),
            rows=rows,
            row_count=data.row_count,
            iteration=iteration,
        )
        return (
            {
                "sql": data.sql,
                "fields": list(data.fields),
                "row_count": data.row_count,
                "sample_rows": [dict(row) for row in data.sample_rows],
                "artifact_ref": result_ref.artifact_ref.model_dump(mode="json"),
                "result_set_id": result_ref.result_set_id,
                "sql_source": "semantic",
                "execution_ms": data.execution_ms,
                "attempt": result.attempt,
            },
            rows,
        )

    def _register_compute_result(
        self,
        state: AgentRuntimeState,
        plan_id: str,
        task: ComputeTask,
        computed: ComputeExecution,
        *,
        attempt: int,
        iteration: int = 0,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        result_store = state.context.result_store
        if result_store is None:
            raise PlanPipelineError("PLAN_RESULT_STORE_REQUIRED")
        iteration = iteration or int(state.context.state.get("current_plan_batch") or 0)
        rows = [dict(row) for row in computed.rows]
        result_ref = result_store.register(
            execution_id=self._required_execution_id(state),
            execution_type=ChatRecordExecutionType.AGENT,
            chat_id=self._required_chat_id(state),
            record_id=self._required_record_id(state),
            plan_id=plan_id,
            node_id=task.id,
            kind=ResultSetKind.COMPUTE,
            fields=list(computed.fields),
            rows=rows,
            row_count=computed.row_count,
            attempt=attempt,
            source_sql=computed.sql,
            idempotency_key=(
                f"plan-compute:{self._required_execution_id(state)}:"
                f"{plan_id}:{task.id}:{attempt}"
            ),
        )
        self._merge_result_ref(state, result_ref)
        self._register_compute_evidence(
            state,
            plan_id,
            task,
            result_ref=result_ref,
            fields=list(computed.fields),
            rows=rows,
            row_count=computed.row_count,
            iteration=iteration,
        )
        return (
            {
                "sql": computed.sql,
                "fields": list(computed.fields),
                "row_count": computed.row_count,
                "sample_rows": rows[:10],
                "artifact_ref": result_ref.artifact_ref.model_dump(mode="json"),
                "result_set_id": result_ref.result_set_id,
                "sql_source": "computed",
                "attempt": attempt,
            },
            rows,
        )

    def _register_query_evidence(
        self,
        state: AgentRuntimeState,
        plan_id: str,
        task: QueryTask,
        *,
        result_ref: ResultSetRef,
        fields: list[str],
        rows: list[dict[str, Any]],
        row_count: int,
        iteration: int,
    ) -> None:
        if self._is_research_execution(state):
            return
        requirement = self._query_requirement_payload(state, task.source_requirement_id)
        metric_refs = tuple(
            str(item.get("ref"))
            for item in ((requirement.get("metrics") or ()) if requirement else ())
            if isinstance(item, dict) and isinstance(item.get("ref"), str)
        )
        dimension_refs = tuple(
            str(item.get("ref"))
            for item in ((requirement.get("group_by") or ()) if requirement else ())
            if isinstance(item, dict) and isinstance(item.get("ref"), str)
        )
        filters = tuple(
            item
            for item in ((requirement.get("filters") or ()) if requirement else ())
            if isinstance(item, dict)
        )
        time_roles = self._time_roles(requirement)
        self._register_unified_evidence(
            state,
            build_analysis_evidence(
                run_id=self._analysis_evidence_run_id(state),
                mode="plan",
                plan_id=plan_id,
                node_id=task.id,
                tool_call_id=f"query:{task.id}",
                result_set_id=result_ref.result_set_id,
                fields=fields,
                rows=rows,
                row_count=row_count,
                metric_refs=metric_refs,
                dimension_refs=dimension_refs,
                time_roles=time_roles,
                filters=filters,
                purpose=f"执行计划查询节点 {task.id}",
                iteration=iteration,
                version_snapshot=self._evidence_version_snapshot(state),
            ),
        )

    def _register_compute_evidence(
        self,
        state: AgentRuntimeState,
        plan_id: str,
        task: ComputeTask,
        *,
        result_ref: ResultSetRef,
        fields: list[str],
        rows: list[dict[str, Any]],
        row_count: int,
        iteration: int,
    ) -> None:
        if self._is_research_execution(state):
            return
        registry = EvidenceRegistry(state.context.state)
        dependencies: list[AnalysisEvidenceDependency] = []
        metric_refs: list[str] = []
        dimension_refs: list[str] = []
        for input_id in task.inputs:
            source_id = f"result:{plan_id}:{input_id}"
            source = next(
                (
                    item
                    for item in registry.evidences()
                    if item.result_set_id == source_id
                ),
                None,
            )
            if source is None:
                raise PlanPipelineError("PLAN_INPUT_EVIDENCE_MISSING")
            dependencies.append(
                AnalysisEvidenceDependency(
                    evidence_id=source.evidence_id,
                    relation=task.operation.value,
                    source_iteration=source.iteration,
                )
            )
            metric_refs.extend(source.metric_refs)
            dimension_refs.extend(source.dimension_refs)
        self._register_unified_evidence(
            state,
            build_analysis_evidence(
                run_id=self._analysis_evidence_run_id(state),
                mode="plan",
                plan_id=plan_id,
                node_id=task.id,
                tool_call_id=f"compute:{task.id}",
                result_set_id=result_ref.result_set_id,
                fields=fields,
                rows=rows,
                row_count=row_count,
                metric_refs=tuple(dict.fromkeys(metric_refs)),
                dimension_refs=tuple(dict.fromkeys(dimension_refs)),
                purpose=f"执行计划计算节点 {task.id}",
                iteration=iteration,
                dependencies=dependencies,
                version_snapshot=self._evidence_version_snapshot(state),
            ),
        )

    @staticmethod
    def _register_unified_evidence(
        state: AgentRuntimeState,
        evidence: Any,
    ) -> None:
        EvidenceRegistry(state.context.state).register(evidence)

    @staticmethod
    def _is_research_execution(state: AgentRuntimeState) -> bool:
        return isinstance(state.context.state.get("research_run_id"), str)

    def _analysis_evidence_run_id(self, state: AgentRuntimeState) -> str:
        """统一使用工具与 ResultStore 已绑定的 Agent 执行 ID。"""

        return self._required_execution_id(state)

    @staticmethod
    def _query_requirement_payload(
        state: AgentRuntimeState,
        requirement_id: str | None,
    ) -> dict[str, Any] | None:
        if not isinstance(requirement_id, str):
            return None
        execution = state.context.state.get("execution_requirement")
        if not isinstance(execution, dict):
            return None
        for item in execution.get("query_requirements") or ():
            if isinstance(item, dict) and item.get("id") == requirement_id:
                return item
        return None

    @staticmethod
    def _time_roles(requirement: dict[str, Any] | None) -> tuple[str, ...]:
        time = requirement.get("time") if isinstance(requirement, dict) else None
        if not isinstance(time, dict):
            return ()
        role = time.get("role")
        return (str(role),) if isinstance(role, str) and role else ()

    def _evidence_version_snapshot(self, state: AgentRuntimeState) -> Any:
        execution = state.context.state.get("execution_requirement")
        asset_snapshot = (
            execution.get("asset_snapshot")
            if isinstance(execution, dict)
            else {}
        )
        semantic_scope = state.context.state.get("semantic_scope")
        return build_analysis_version_snapshot(
            asset_snapshot=asset_snapshot if isinstance(asset_snapshot, dict) else {},
            semantic_scope=(
                semantic_scope if isinstance(semantic_scope, dict) else {}
            ),
        )

    @staticmethod
    def _merge_result_ref(state: AgentRuntimeState, result_ref: ResultSetRef) -> None:
        result_sets = state.context.state.get("result_sets")
        if not isinstance(result_sets, dict):
            result_sets = {}
        state.context.state["result_sets"] = {
            **result_sets,
            result_ref.result_set_id: result_ref.model_dump(mode="json"),
        }

    @staticmethod
    def _set_task_state(
        states: dict[str, dict[str, Any]],
        task_id: str,
        status: AnalysisTaskExecutionStatus,
        *,
        increment_attempt: bool = False,
        **details: Any,
    ) -> None:
        current = states[task_id]
        states[task_id] = {
            **current,
            "status": status.value,
            "attempt": int(current.get("attempt") or 0) + int(increment_attempt),
            **details,
        }

    @staticmethod
    def _task_event_payload(
        state: AgentRuntimeState,
        plan_id: str,
        task_id: str,
        status: AnalysisTaskExecutionStatus,
        **details: Any,
    ) -> dict[str, Any]:
        return {
            "record_id": state.record.id,
            "run_id": state.require_run_id(),
            "plan_id": plan_id,
            "task_id": task_id,
            "status": status.value,
            **details,
        }

    @staticmethod
    def _semantic_refs(state: AgentRuntimeState) -> list[dict[str, Any]]:
        scope = state.context.state.get("semantic_scope")
        if not isinstance(scope, dict):
            return []
        return [
            dict(item)
            for item in scope.get("allowed_assets") or []
            if isinstance(item, dict)
        ]

    @staticmethod
    def _required_execution_id(state: AgentRuntimeState) -> str:
        value = state.context.execution_id
        if not isinstance(value, str) or not value:
            raise PlanPipelineError("PLAN_EXECUTION_ID_REQUIRED")
        return value

    @staticmethod
    def _required_chat_id(state: AgentRuntimeState) -> int:
        value = state.context.chat_id
        if not isinstance(value, int) or isinstance(value, bool):
            raise PlanPipelineError("PLAN_RESULT_OWNERSHIP_REQUIRED")
        return value

    @staticmethod
    def _required_record_id(state: AgentRuntimeState) -> int:
        value = state.context.record_id
        if not isinstance(value, int) or isinstance(value, bool):
            raise PlanPipelineError("PLAN_RESULT_OWNERSHIP_REQUIRED")
        return value

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
        return bool(
            scope is not None
            and scope.decision_status is not None
            and scope.decision_status.value == "ambiguous"
        )

    @staticmethod
    def _load_execution_requirement(state: AgentRuntimeState) -> ExecutionRequirement:
        """读取路由阶段产物；Plan 不再自行检索或重新绑定语义资产。"""

        try:
            return execution_requirement_from_state(state.context.state)
        except ValueError as exc:
            raise PlanPipelineError(str(exc)) from exc

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
            query_plans = scope.query_plans or (
                (scope.query_plan,) if scope.query_plan else ()
            )
            if not query_plans:
                raise PlanPipelineError("PLAN_STRICT_QUERY_PLAN_MISSING")
            task_spec = getattr(task, "spec", None)
            selected_plan = None
            source_requirement_id = getattr(task, "source_requirement_id", None)
            if source_requirement_id is not None:
                selected_plan = next(
                    (
                        candidate
                        for candidate in query_plans
                        if candidate.query_shape.get("source_requirement_id")
                        == source_requirement_id
                    ),
                    None,
                )
                if selected_plan is None:
                    # 带来源 ID 的新契约节点禁止退回资产签名或索引匹配。
                    raise PlanPipelineError("PLAN_STRICT_SOURCE_REQUIREMENT_NOT_MATCHED")
            if task_spec is not None:
                selected_plan = selected_plan or next(
                    (
                        candidate
                        for candidate in query_plans
                        if {item.metric_id for item in candidate.metrics}
                        == set(task_spec.metric_ids)
                        and {
                            item.physical_dimension_id for item in candidate.dimensions
                        }
                        == set(task_spec.dimension_ids)
                        and _time_ranges_equal(
                            _task_time_range(
                                task_spec,
                                candidate.time_binding.dimension_id,
                            ),
                            candidate.time_binding.time_range,
                        )
                    ),
                    None,
                )
            if (
                selected_plan is None
                and getattr(task, "source_requirement_id", None) is not None
            ):
                # 新契约节点必须按来源需求精确匹配，不能退回索引或唯一计划兜底。
                raise PlanPipelineError("PLAN_STRICT_SOURCE_REQUIREMENT_NOT_MATCHED")
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
            output_aliases = getattr(task_spec, "output_aliases", None)
            if isinstance(output_aliases, dict) and output_aliases:
                selected_plan = selected_plan.model_copy(
                    update={"output_aliases": dict(output_aliases)}
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
                "output_aliases": dict(task.spec.output_aliases),
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
        # 严格计划覆盖 group_by、where 和 having 的全部维度（见
        # prepare_strict_query_scope），任务签名必须使用同一口径，
        # 否则带维度值过滤的查询会被误判为缺少计划。
        def _task_dimension_ids(task: QueryTask) -> tuple[int, ...]:
            filter_dim_ids: set[int] = set()
            for item in (*task.spec.filters, *task.spec.having):
                asset_id = item.get("asset_id")
                if isinstance(asset_id, int):
                    filter_dim_ids.add(asset_id)
            return tuple(sorted({*task.spec.dimension_ids, *filter_dim_ids}))

        task_signatures = {
            (
                tuple(sorted(task.spec.metric_ids)),
                _task_dimension_ids(task),
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
        if (
            query_task_count is not None
            and len(strict_query_plans) < query_task_count
            and not task_signatures
        ):
            raise PlanPipelineError("PLAN_STRICT_QUERY_PLAN_MISSING")
        if any(plan.validation_status.value != "PROVEN" for plan in strict_query_plans):
            raise PlanPipelineError("PLAN_STRICT_QUERY_PLAN_NOT_PROVEN")

    @staticmethod
    def _result_set_id_for_node(
        result_sets: dict[str, Any],
        plan_id: str,
        node_id: str,
    ) -> str | None:
        for result_set_id, payload in result_sets.items():
            if (
                isinstance(payload, dict)
                and payload.get("plan_id") == plan_id
                and payload.get("node_id") == node_id
            ):
                return str(result_set_id)
        return None

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
            return cast(ToolResult[Any], projection.result)

    def _save_plan(self, state: AgentRuntimeState, plan: AnalysisPlan) -> None:
        state.context.state["analysis_plan"] = plan.model_dump(mode="json")
        self._persist_state(state)
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
    @staticmethod
    def _persist_state(state: AgentRuntimeState) -> None:
        # 合并而不是整表替换：行上可能已有其他写入方落下的键——shadow 行的
        # ``shadow`` 标记、研究循环的 research_run_snapshot 等。run 1306 演练
        # 教训：整表替换曾把 shadow 标记抹掉，终态收口与就绪扫描都看不见该行。
        derived = dict(state.run.derived_state or {})
        derived.update(state.persistable_context())
        agent_run_repository.update_run(
            state.context.session,
            state.run,
            derived_state=derived,
        )
        state.context.session.commit()



__all__ = [
    "AnalysisExecutionService",
    "PlanExecutionOutcome",
    "AnalysisExecutionDependencies",
    "PlanPipelineError",
]

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
