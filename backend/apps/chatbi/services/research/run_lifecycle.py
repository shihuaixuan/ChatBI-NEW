"""Research Run 的统一持久化提交边界、恢复与取消服务（阶段 4）。

对应 doc38 §8.4.2 / §8.4.5 / §8.4.6：

- ``ResearchToolCallCommit``：单个工具调用的提交边界。进入时创建 RUNNING
  事实行并立即提交（进程崩溃后可被恢复发现）；``finish()`` 记录终态事实
  （Tool Call 终态、Observation、Snapshot 更新），退出时在同一事务一次性
  提交。外部查询成功但 Artifact 写入失败时，工具已经转成结构化失败观察，
  这里保证不会登记 Evidence。
- ``recover_research_run``：从 ``derived_state`` 加载冻结 Requirement 和
  研究事实（不重新解析当前发布 Schema），收口中断的 Step，修复或标记
  RUNNING Tool Call，校验证据 DAG 后返回可继续执行的 Context。
- ``cancel_research_run``：把未完成 Tool Call 标记 INTERRUPTED，保留已
  持久化证据，写入 cancelled completion 并刷新快照。Run / ChatRecord 的
  终态仍由通用生命周期（AgentLifecycle.finalize_cancellation）负责。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

import orjson

from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchCompletion,
    ResearchCompletionReason,
    ResearchRunSnapshot,
    ToolErrorCode,
    ToolFailureStage,
    ToolObservation,
    ToolObservationStatus,
)
from apps.chatbi.models.orm.agent_run import (
    AgentToolCallStatus,
    ChatbiAgentRun,
    ChatbiAgentToolCall,
)
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.research.state_snapshot import (
    build_research_run_snapshot,
)
from apps.chatbi.services.research.tool_context import (
    RESEARCH_STATE_KEY,
    ResearchToolContext,
)
from apps.tool import ToolResult, ToolStatus

_RESULT_SETS_KEY = "result_sets"
_SNAPSHOT_KEY = "research_run_snapshot"

_MAX_SUMMARY_CHARS = 2000


def _require_id(value: int | None, code: str) -> int:
    if value is None or value <= 0:
        raise ValueError(code)
    return value


# ---------------------------------------------------------------------- #
# 受控摘要
# ---------------------------------------------------------------------- #


def _prune(value: Any, *, list_keep: int, string_cap: int) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _prune(item, list_keep=list_keep, string_cap=string_cap)
            for key, item in value.items()
        }
    if isinstance(value, list):
        pruned_items = [
            _prune(item, list_keep=list_keep, string_cap=string_cap)
            for item in value[:list_keep]
        ]
        if len(value) > list_keep:
            pruned_items.append({"_truncated_count": len(value)})
        return pruned_items
    if isinstance(value, str) and len(value) > string_cap:
        return value[:string_cap]
    return value


def _bounded_summary(value: dict[str, Any]) -> dict[str, Any]:
    """工具事实行的摘要只保存有界内容，完整结果留在 ResultStore。"""

    try:
        encoded = orjson.dumps(value).decode()
    except (TypeError, orjson.JSONEncodeError):
        return {"_unserializable": True}
    if len(encoded) <= _MAX_SUMMARY_CHARS:
        return value
    for list_keep, string_cap in ((3, 120), (1, 60), (0, 30)):
        pruned = _prune(value, list_keep=list_keep, string_cap=string_cap)
        pruned["_truncated"] = True
        if len(orjson.dumps(pruned).decode()) <= _MAX_SUMMARY_CHARS:
            return cast(dict[str, Any], pruned)
    return {"_truncated": True, "keys": sorted(str(key) for key in value)}


def _observation_summary(observation: ToolObservation) -> dict[str, Any]:
    succeeded = observation.status is ToolObservationStatus.SUCCEEDED
    return _bounded_summary(
        {
            "success": succeeded,
            "status": observation.status.value,
            "error_code": (
                observation.error_code.value
                if observation.error_code is not None and not succeeded
                else None
            ),
            "evidence_count": len(observation.evidence_ids),
            "result_count": len(observation.result_ids),
            "statistics": observation.statistics,
            "limitations": list(observation.limitations)[:4],
        }
    )


# ---------------------------------------------------------------------- #
# Observation 归一
# ---------------------------------------------------------------------- #


def _observation_of(result: ToolResult[Any]) -> ToolObservation | None:
    data = result.data
    return data if isinstance(data, ToolObservation) else None


def _synthetic_observation(
    ctx: ResearchToolContext,
    tool_call_id: str,
    tool_name: str,
    result: ToolResult[Any],
) -> ToolObservation:
    """为未产出研究观察的终态结果（Registry 拒绝、取消等）补一条失败事实。"""

    if result.status is ToolStatus.REJECTED:
        code = ToolErrorCode.INVALID_REQUEST
        stage = ToolFailureStage.VALIDATION
    elif result.status is ToolStatus.INTERRUPTED:
        code = ToolErrorCode.CANCELLED
        stage = ToolFailureStage.EXECUTION
    else:
        code = ToolErrorCode.EXECUTION_FAILED
        stage = ToolFailureStage.EXECUTION
    return ToolObservation(
        run_id=ctx.run_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        status=ToolObservationStatus.FAILED,
        failure_stage=stage,
        error_code=code,
        error_category="domain",
        message=(result.model_content or "工具调用未产出研究观察")[:2000],
        details={
            "reason": "non_research_tool_result",
            "result_status": result.status.value,
        },
    )


def _row_status(observation: ToolObservation) -> AgentToolCallStatus:
    if observation.status is ToolObservationStatus.SUCCEEDED:
        return AgentToolCallStatus.SUCCEEDED
    return AgentToolCallStatus.FAILED


def _row_error_code(observation: ToolObservation) -> str | None:
    if observation.status is ToolObservationStatus.SUCCEEDED:
        return None
    if observation.error_code is None:
        return "tool_undeclared_exception"
    return observation.error_code.value


# ---------------------------------------------------------------------- #
# 统一提交边界（§8.4.2）
# ---------------------------------------------------------------------- #


class ResearchToolCallCommit:
    """一次研究工具调用的持久化边界。

    用法（阶段 5 Harness 的每次模型工具调用）::

        with ResearchToolCallCommit(session, run, step_id=step.id,
                                    tool_call_id=call.call_id,
                                    tool_name=call.name,
                                    args_summary=call.args,
                                    ctx=ctx) as commit:
            result = registry.execute(call, ctx)
            commit.finish(result)

    - ``__enter__``：创建 RUNNING 事实行并提交（崩溃可见，供恢复定位）；
    - ``finish(result)``：写 Tool Call 终态、合并研究状态、重建快照；
      不提交；
    - ``__exit__``：单事务提交。执行体抛出未知异常时把该调用标 FAILED
      后提交再传播原异常；``finish`` 之后提交失败则回滚，保持“RUNNING 行
      ⇒ 无已提交终态事实”的不变式，交给恢复流程修复。
    """

    def __init__(
        self,
        session: Any,
        run_row: ChatbiAgentRun,
        *,
        step_id: int,
        tool_call_id: str,
        tool_name: str,
        args_summary: Mapping[str, Any],
        ctx: ResearchToolContext,
    ) -> None:
        self._session = session
        self._run_row = run_row
        self._run_db_id = _require_id(run_row.id, "RESEARCH_COMMIT_RUN_ID_REQUIRED")
        self._step_id = step_id
        self._tool_call_id = tool_call_id
        self._tool_name = tool_name
        self._args_summary = args_summary
        self._ctx = ctx
        self.row: ChatbiAgentToolCall | None = None
        self._finished = False

    def __enter__(self) -> ResearchToolCallCommit:
        self.row = agent_run_repository.start_tool_call(
            self._session,
            run_id=self._run_db_id,
            step_id=self._step_id,
            tool_call_id=self._tool_call_id,
            tool_name=self._tool_name,
            args_summary=_bounded_summary(dict(self._args_summary)),
        )
        self._session.commit()
        return self

    def finish(self, result: ToolResult[Any]) -> ToolObservation | None:
        """记录终态事实；等待 ``__exit__`` 与其他写入同一事务提交。"""

        if self.row is None:
            raise TypeError("RESEARCH_COMMIT_NOT_ENTERED")
        observation = _observation_of(result)
        if observation is None and result.status is not ToolStatus.SUCCEEDED:
            observation = _synthetic_observation(
                self._ctx, self._tool_call_id, self._tool_name, result
            )
            self._ctx.record_observation(observation)
        if observation is not None:
            row_status = _row_status(observation)
            error_code = _row_error_code(observation)
            summary = _observation_summary(observation)
        else:
            row_status = AgentToolCallStatus.SUCCEEDED
            error_code = None
            summary = {
                "success": True,
                "status": ToolObservationStatus.SUCCEEDED.value,
            }
        agent_run_repository.finish_tool_call(
            self._session,
            self.row,
            status=row_status,
            result_summary=summary,
            error_code=error_code,
        )
        self._persist_state()
        self._finished = True
        return observation

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> Literal[False]:
        body_failed = exc_type is not None
        try:
            if body_failed and not self._finished and self.row is not None:
                agent_run_repository.finish_tool_call(
                    self._session,
                    self.row,
                    status=AgentToolCallStatus.FAILED,
                    result_summary={
                        "success": False,
                        "status": AgentToolCallStatus.FAILED.value,
                        "error_code": "tool_undeclared_exception",
                    },
                    error_code="tool_undeclared_exception",
                )
                self._persist_state()
            self._session.commit()
        except Exception:
            # finish 后提交失败（例如 Snapshot 超限、数据库故障）：回滚，
            # Artifact 可能已写入但不会出现“一边成功一边被当作完整成功”。
            self._session.rollback()
            if not body_failed:
                raise
        return False

    def _persist_state(self) -> None:
        derived = dict(self._run_row.derived_state or {})
        research_state = self._ctx.context.state.get(RESEARCH_STATE_KEY)
        if isinstance(research_state, dict):
            derived[RESEARCH_STATE_KEY] = research_state
        result_sets = self._ctx.context.state.get(_RESULT_SETS_KEY)
        if isinstance(result_sets, dict):
            merged = dict(derived.get(_RESULT_SETS_KEY) or {})
            merged.update(result_sets)
            derived[_RESULT_SETS_KEY] = merged
        running_ids = [
            item.tool_call_id
            for item in agent_run_repository.list_running_tool_calls(
                self._session, self._run_db_id
            )
            if item.tool_call_id != self._tool_call_id
        ]
        snapshot = build_research_run_snapshot(
            self._ctx,
            running_tool_call_ids=running_ids,
            agent_run_id=self._run_db_id,
            premise_result=self._ctx.premise_result,
        )
        derived[_SNAPSHOT_KEY] = snapshot.model_dump(mode="json")
        agent_run_repository.update_run(
            self._session,
            self._run_row,
            derived_state=derived,
        )


# ---------------------------------------------------------------------- #
# 恢复（§8.4.5）
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResearchRecoveryReport:
    """一次恢复的事实清单，供审计和 Harness 决定下一步。"""

    closed_steps: tuple[int, ...]
    repaired_tool_calls: tuple[str, ...]
    interrupted_tool_calls: tuple[str, ...]
    resumed_iteration: int
    resumed_from_snapshot: bool


def load_research_state(derived_state: Mapping[str, Any] | None) -> dict[str, Any]:
    if not derived_state:
        return {}
    research_state = derived_state.get(RESEARCH_STATE_KEY)
    if research_state is None:
        return {}
    if not isinstance(research_state, dict):
        raise ValueError("RESEARCH_RECOVERY_STATE_INVALID")
    return research_state


def load_frozen_requirement(
    research_state: Mapping[str, Any],
) -> ResearchAgentRequirement:
    """只使用冻结 Requirement；当前发布 Schema 不是运行时事实源。"""

    payload = research_state.get("requirement")
    if not isinstance(payload, dict):
        raise ValueError("RESEARCH_RECOVERY_REQUIREMENT_MISSING")
    return ResearchAgentRequirement.model_validate(payload)


def rebuild_research_context(
    session: Any,
    run_row: ChatbiAgentRun,
    requirement: ResearchAgentRequirement,
    research_state: Mapping[str, Any],
    *,
    semantic_runtime: Any = None,
    compute_engine: Any = None,
    result_store: Any = None,
    cancellation: Any = None,
    trace_recorder: Any = None,
) -> ResearchToolContext:
    agent_context = AgentToolContext(
        session=session,
        oid=int(run_row.oid),
        user_id=run_row.created_by,
        datasource_id=None,
        execution_id=(
            research_state.get("execution_id")
            if isinstance(research_state.get("execution_id"), str)
            else None
        ),
        chat_id=int(run_row.chat_id),
        record_id=int(run_row.record_id),
        dataset_id=(
            research_state.get("dataset_id")
            if isinstance(research_state.get("dataset_id"), int)
            else None
        ),
        result_store=result_store,
        state={
            "research_run_id": requirement.run_id,
            RESEARCH_STATE_KEY: dict(research_state),
            _RESULT_SETS_KEY: {},
        },
    )
    ctx = ResearchToolContext(
        context=agent_context,
        requirement=requirement,
        semantic_runtime=semantic_runtime,
        compute_engine=compute_engine,
        budget=requirement.budget,
        cancellation=cancellation,
        trace_recorder=trace_recorder,
    )
    ctx.bind_to_context()
    return ctx


def recover_research_run(
    session: Any,
    run_row: ChatbiAgentRun,
    *,
    semantic_runtime: Any = None,
    compute_engine: Any = None,
    result_store: Any = None,
    cancellation: Any = None,
    trace_recorder: Any = None,
) -> tuple[ResearchToolContext, ResearchRecoveryReport]:
    """按 §8.4.5 恢复一个中断的 Research Run。

    前置条件是 ``derived_state`` 中存在冻结 Requirement。恢复过程：

    1. 加载冻结 Requirement 和研究事实；
    2. 若存在旧快照，校验其版本与冻结 Requirement 一致；
    3. 收口仍处于 RUNNING 的 Step；
    4. 对每个 RUNNING Tool Call：已有终态 Observation 则完成状态修复，
       否则按幂等语义标记 INTERRUPTED（重试不重复计费由指纹去重保证）；
    5. 校验证据 DAG 和结论引用，刷新快照并在同一事务提交。
    """

    derived = dict(run_row.derived_state or {})
    research_state = load_research_state(derived)
    requirement = load_frozen_requirement(research_state)
    run_db_id = _require_id(run_row.id, "RESEARCH_RECOVERY_RUN_ID_REQUIRED")

    stored_snapshot: dict[str, Any] | None = None
    raw_snapshot = derived.get(_SNAPSHOT_KEY)
    if isinstance(raw_snapshot, dict):
        snapshot = ResearchRunSnapshot.model_validate(raw_snapshot)
        if (
            snapshot.run_id != requirement.run_id
            or snapshot.version_snapshot != requirement.version_snapshot
        ):
            raise ValueError("RESEARCH_RECOVERY_VERSION_CONFLICT")
        stored_snapshot = raw_snapshot

    ctx = rebuild_research_context(
        session,
        run_row,
        requirement,
        research_state,
        semantic_runtime=semantic_runtime,
        compute_engine=compute_engine,
        result_store=result_store,
        cancellation=cancellation,
        trace_recorder=trace_recorder,
    )

    closed_steps: list[int] = []
    running_step = agent_run_repository.get_running_step(session, run_db_id)
    if running_step is not None:
        agent_run_repository.cancel_step(
            session,
            running_step,
            "运行中断，恢复时收口未完成步骤",
        )
        closed_steps.append(int(running_step.step_index))

    repaired: list[str] = []
    interrupted: list[str] = []
    for row in agent_run_repository.list_running_tool_calls(session, run_db_id):
        observation = ctx.observation(row.tool_call_id)
        if observation is not None:
            agent_run_repository.finish_tool_call(
                session,
                row,
                status=_row_status(observation),
                result_summary=_observation_summary(observation),
                error_code=_row_error_code(observation),
            )
            repaired.append(row.tool_call_id)
        else:
            agent_run_repository.finish_tool_call(
                session,
                row,
                status=AgentToolCallStatus.INTERRUPTED,
                result_summary={
                    "success": False,
                    "status": AgentToolCallStatus.INTERRUPTED.value,
                    "error_code": "tool_call_interrupted",
                },
                error_code="tool_call_interrupted",
            )
            interrupted.append(row.tool_call_id)

    refreshed_state = ctx.context.state.get(RESEARCH_STATE_KEY)
    derived[RESEARCH_STATE_KEY] = (
        refreshed_state if isinstance(refreshed_state, dict) else research_state
    )
    result_sets = ctx.context.state.get(_RESULT_SETS_KEY)
    if isinstance(result_sets, dict) and result_sets:
        derived[_RESULT_SETS_KEY] = {
            **dict(derived.get(_RESULT_SETS_KEY) or {}),
            **result_sets,
        }
    refreshed_snapshot = build_research_run_snapshot(
        ctx,
        running_tool_call_ids=[],
        agent_run_id=run_db_id,
        premise_result=ctx.premise_result,
    )
    derived[_SNAPSHOT_KEY] = refreshed_snapshot.model_dump(mode="json")
    agent_run_repository.update_run(session, run_row, derived_state=derived)
    session.commit()

    report = ResearchRecoveryReport(
        closed_steps=tuple(closed_steps),
        repaired_tool_calls=tuple(repaired),
        interrupted_tool_calls=tuple(interrupted),
        resumed_iteration=ctx.iteration,
        resumed_from_snapshot=stored_snapshot is not None,
    )
    return ctx, report


# ---------------------------------------------------------------------- #
# 取消（§8.4.6）
# ---------------------------------------------------------------------- #


def cancel_research_run(
    session: Any,
    run_row: ChatbiAgentRun,
    ctx: ResearchToolContext,
    *,
    reason: str = "用户已请求取消运行",
    stage: str = "user_requested",
    report_draft: str | None = None,
) -> ResearchRunSnapshot:
    """收口研究侧取消事实：未完成调用 INTERRUPTED，证据保留，写入终态。

    只负责研究事实（Tool Call、research_state、Snapshot）；Run 与
    ChatRecord 状态由通用生命周期的取消收口负责。
    """

    run_db_id = _require_id(run_row.id, "RESEARCH_CANCEL_RUN_ID_REQUIRED")
    for row in agent_run_repository.list_running_tool_calls(session, run_db_id):
        agent_run_repository.finish_tool_call(
            session,
            row,
            status=AgentToolCallStatus.INTERRUPTED,
            result_summary={
                "success": False,
                "status": AgentToolCallStatus.INTERRUPTED.value,
                "error_code": "tool_call_interrupted",
                "cancel_stage": stage,
            },
            error_code="tool_call_interrupted",
        )
    if not ctx.finished:
        ctx.finish(
            ResearchCompletion(
                run_id=ctx.run_id,
                status="cancelled",
                reason=ResearchCompletionReason.CANCELLED,
                summary=reason[:4000],
            )
        )
    derived = dict(run_row.derived_state or {})
    research_state = ctx.context.state.get(RESEARCH_STATE_KEY)
    if isinstance(research_state, dict):
        derived[RESEARCH_STATE_KEY] = research_state
    result_sets = ctx.context.state.get(_RESULT_SETS_KEY)
    if isinstance(result_sets, dict) and result_sets:
        derived[_RESULT_SETS_KEY] = {
            **dict(derived.get(_RESULT_SETS_KEY) or {}),
            **result_sets,
        }
    snapshot = build_research_run_snapshot(
        ctx,
        running_tool_call_ids=[],
        agent_run_id=run_db_id,
        premise_result=ctx.premise_result,
        report_draft=report_draft,
    )
    derived[_SNAPSHOT_KEY] = snapshot.model_dump(mode="json")
    agent_run_repository.update_run(session, run_row, derived_state=derived)
    session.commit()
    return snapshot


__all__ = [
    "RESEARCH_STATE_KEY",
    "ResearchRecoveryReport",
    "ResearchToolCallCommit",
    "cancel_research_run",
    "load_frozen_requirement",
    "load_research_state",
    "rebuild_research_context",
    "recover_research_run",
]
