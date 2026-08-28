"""Research Agent Run 的状态恢复、取消和上下文重建。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import orjson

from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchStateSnapshot,
    ToolResultStatus,
)
from apps.chatbi.models.orm.agent_run import (
    AgentToolCallStatus,
    ChatbiAgentRun,
)
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.evidence import ANALYSIS_EVIDENCE_REGISTRY_KEY
from apps.chatbi.services.research.state_snapshot import (
    RESEARCH_STATE_SNAPSHOT_KEY,
    build_research_state_snapshot,
    load_research_state_snapshot,
    validate_research_state_snapshot,
)
from apps.chatbi.services.research.tool_context import (
    RESEARCH_STATE_KEY,
    ResearchToolContext,
)

_RESULT_SETS_KEY = "result_sets"
_MAX_SUMMARY_CHARS = 2_000


@dataclass(frozen=True)
class ResearchRecoveryReport:
    """一次恢复所收口和修复的持久化事实。"""

    closed_steps: tuple[int, ...]
    repaired_tool_calls: tuple[str, ...]
    interrupted_tool_calls: tuple[str, ...]
    resumed_iteration: int
    resumed_from_snapshot: bool


def _require_id(value: int | None, code: str) -> int:
    if value is None or value <= 0:
        raise ValueError(code)
    return value


def _prune(value: Any, *, list_keep: int, string_cap: int) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _prune(item, list_keep=list_keep, string_cap=string_cap)
            for key, item in value.items()
        }
    if isinstance(value, list):
        items = [
            _prune(item, list_keep=list_keep, string_cap=string_cap)
            for item in value[:list_keep]
        ]
        if len(value) > list_keep:
            items.append({"_truncated_count": len(value)})
        return items
    if isinstance(value, str) and len(value) > string_cap:
        return value[:string_cap]
    return value


def _bounded_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    """保存受控工具结果摘要，完整结果仍保留在 ResearchState 或 ResultStore。"""

    payload = dict(value)
    try:
        if len(orjson.dumps(payload)) <= _MAX_SUMMARY_CHARS:
            return payload
    except (TypeError, orjson.JSONEncodeError):
        return {"_unserializable": True}
    for list_keep, string_cap in ((3, 120), (1, 60), (0, 30)):
        pruned = _prune(payload, list_keep=list_keep, string_cap=string_cap)
        if not isinstance(pruned, dict):
            continue
        pruned["_truncated"] = True
        if len(orjson.dumps(pruned)) <= _MAX_SUMMARY_CHARS:
            return pruned
    return {"_truncated": True, "keys": sorted(str(key) for key in payload)}


def load_research_state(derived_state: Mapping[str, Any] | None) -> dict[str, Any]:
    """读取新 ResearchState；缺少状态时明确失败。"""

    if not derived_state:
        return {}
    state = derived_state.get(RESEARCH_STATE_KEY)
    if state is None:
        return {}
    if not isinstance(state, dict):
        raise ValueError("RESEARCH_AGENT_RECOVERY_STATE_INVALID")
    return state


def load_frozen_requirement(
    research_state: Mapping[str, Any],
) -> ResearchAgentRequirement:
    """只从持久化状态加载冻结 Requirement。"""

    payload = research_state.get("requirement")
    if not isinstance(payload, dict):
        raise ValueError("RESEARCH_AGENT_RECOVERY_REQUIREMENT_MISSING")
    return ResearchAgentRequirement.model_validate(payload)


def rebuild_research_context(
    session: Any,
    run_row: ChatbiAgentRun,
    requirement: ResearchAgentRequirement,
    research_state: Mapping[str, Any],
    analysis_evidence: Mapping[str, Any] | None = None,
    *,
    semantic_runtime: Any = None,
    compute_engine: Any = None,
    result_store: Any = None,
    cancellation: Any = None,
    trace_recorder: Any = None,
    semantic_retrieval_service: Any = None,
    context_state_overlay: Mapping[str, Any] | None = None,
) -> ResearchToolContext:
    """从新 ResearchState 重建工具上下文，不创建旧计划状态。"""

    execution_id = research_state.get("execution_id")
    dataset_id = research_state.get("dataset_id")
    agent_context = AgentToolContext(
        session=session,
        oid=int(run_row.oid),
        user_id=run_row.created_by,
        datasource_id=None,
        execution_id=execution_id if isinstance(execution_id, str) else None,
        chat_id=int(run_row.chat_id),
        record_id=int(run_row.record_id),
        dataset_id=dataset_id if isinstance(dataset_id, int) else None,
        result_store=result_store,
        state={
            **dict(context_state_overlay or {}),
            "research_run_id": requirement.run_id,
            RESEARCH_STATE_KEY: dict(research_state),
            _RESULT_SETS_KEY: {},
            ANALYSIS_EVIDENCE_REGISTRY_KEY: dict(analysis_evidence or {}),
        },
    )
    ctx = ResearchToolContext(
        context=agent_context,
        requirement=requirement,
        semantic_runtime=semantic_runtime,
        semantic_retrieval_service=semantic_retrieval_service,
        compute_engine=compute_engine,
        budget=requirement.budget,
        cancellation=cancellation,
        trace_recorder=trace_recorder,
    )
    ctx.bind_to_context()
    return ctx


def _stored_tool_result_repair(
    ctx: ResearchToolContext,
    tool_call_id: str,
) -> tuple[AgentToolCallStatus, dict[str, Any]] | None:
    """读取已保存的 ToolResult，修复仍为 RUNNING 的工具调用事实。"""

    payload = ctx.research_tool_result(tool_call_id)
    if not isinstance(payload, dict):
        return None
    raw_result = payload.get("tool_result")
    if not isinstance(raw_result, dict):
        raise ValueError("RESEARCH_AGENT_RECOVERY_TOOL_RESULT_INVALID")
    raw_status = raw_result.get("status")
    if not isinstance(raw_status, str):
        raise ValueError("RESEARCH_AGENT_RECOVERY_TOOL_RESULT_INVALID")
    status_map = {
        ToolResultStatus.SUCCEEDED.value: AgentToolCallStatus.SUCCEEDED,
        ToolResultStatus.FAILED.value: AgentToolCallStatus.FAILED,
        ToolResultStatus.WAITING_FOR_USER.value: AgentToolCallStatus.WAITING_FOR_USER,
    }
    status = status_map.get(raw_status)
    if status is None:
        raise ValueError("RESEARCH_AGENT_RECOVERY_TOOL_RESULT_INVALID")
    error = raw_result.get("error")
    error_code = error.get("code") if isinstance(error, dict) else None
    return status, {
        "tool_result": raw_result,
        "status": raw_status,
        "error_code": error_code,
    }


def recover_research_agent_run(
    session: Any,
    run_row: ChatbiAgentRun,
    *,
    semantic_runtime: Any = None,
    semantic_retrieval_service: Any = None,
    compute_engine: Any = None,
    result_store: Any = None,
    cancellation: Any = None,
    trace_recorder: Any = None,
    context_state_overlay: Mapping[str, Any] | None = None,
) -> tuple[ResearchToolContext, ResearchRecoveryReport]:
    """只按新 ResearchState 和 ResearchStateSnapshot 恢复运行。"""

    derived = dict(run_row.derived_state or {})
    research_state = load_research_state(derived)
    if not research_state:
        raise ValueError("RESEARCH_AGENT_RECOVERY_STATE_MISSING")
    requirement = load_frozen_requirement(research_state)
    run_db_id = _require_id(run_row.id, "RESEARCH_AGENT_RECOVERY_RUN_ID_REQUIRED")
    snapshot = load_research_state_snapshot(derived)
    input_ref = research_state.get("agent_input_ref")
    if isinstance(input_ref, str) and snapshot.input_snapshot_ref != input_ref:
        raise ValueError("RESEARCH_AGENT_STATE_SNAPSHOT_INPUT_REF_MISMATCH")

    analysis = derived.get(ANALYSIS_EVIDENCE_REGISTRY_KEY)
    ctx = rebuild_research_context(
        session,
        run_row,
        requirement,
        research_state,
        analysis_evidence=analysis if isinstance(analysis, dict) else None,
        semantic_runtime=semantic_runtime,
        semantic_retrieval_service=semantic_retrieval_service,
        compute_engine=compute_engine,
        result_store=result_store,
        cancellation=cancellation,
        trace_recorder=trace_recorder,
        context_state_overlay=context_state_overlay,
    )
    ctx.replay_research_state_events()
    validate_research_state_snapshot(snapshot, ctx)

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
        repair = _stored_tool_result_repair(ctx, row.tool_call_id)
        if repair is None:
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
            continue
        status, summary = repair
        agent_run_repository.finish_tool_call(
            session,
            row,
            status=status,
            result_summary=_bounded_summary(summary),
            error_code=summary.get("error_code"),
        )
        repaired.append(row.tool_call_id)

    refreshed_state = ctx.context.state.get(RESEARCH_STATE_KEY)
    if not isinstance(refreshed_state, dict):
        raise ValueError("RESEARCH_AGENT_RECOVERY_STATE_INVALID")
    derived[RESEARCH_STATE_KEY] = refreshed_state
    result_sets = ctx.context.state.get(_RESULT_SETS_KEY)
    if isinstance(result_sets, dict) and result_sets:
        derived[_RESULT_SETS_KEY] = {
            **dict(derived.get(_RESULT_SETS_KEY) or {}),
            **result_sets,
        }
    analysis = ctx.context.state.get(ANALYSIS_EVIDENCE_REGISTRY_KEY)
    if isinstance(analysis, dict):
        derived[ANALYSIS_EVIDENCE_REGISTRY_KEY] = dict(analysis)
    derived[RESEARCH_STATE_SNAPSHOT_KEY] = build_research_state_snapshot(
        ctx
    ).model_dump(mode="json")
    agent_run_repository.update_run(session, run_row, derived_state=derived)
    session.commit()

    return ctx, ResearchRecoveryReport(
        closed_steps=tuple(closed_steps),
        repaired_tool_calls=tuple(repaired),
        interrupted_tool_calls=tuple(interrupted),
        resumed_iteration=ctx.iteration,
        resumed_from_snapshot=True,
    )


def cancel_research_run(
    session: Any,
    run_row: ChatbiAgentRun,
    ctx: ResearchToolContext,
    *,
    reason: str = "用户已请求取消运行",
    stage: str = "user_requested",
) -> ResearchStateSnapshot:
    """收口新 ResearchState 的取消状态并保留已完成 Evidence。"""

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
    del reason
    ctx.set_current_status("cancelled")
    snapshot = build_research_state_snapshot(ctx)
    derived = dict(run_row.derived_state or {})
    derived[RESEARCH_STATE_KEY] = ctx.context.state[RESEARCH_STATE_KEY]
    result_sets = ctx.context.state.get(_RESULT_SETS_KEY)
    if isinstance(result_sets, dict) and result_sets:
        derived[_RESULT_SETS_KEY] = {
            **dict(derived.get(_RESULT_SETS_KEY) or {}),
            **result_sets,
        }
    analysis = ctx.context.state.get(ANALYSIS_EVIDENCE_REGISTRY_KEY)
    if isinstance(analysis, dict):
        derived[ANALYSIS_EVIDENCE_REGISTRY_KEY] = dict(analysis)
    derived[RESEARCH_STATE_SNAPSHOT_KEY] = snapshot.model_dump(mode="json")
    agent_run_repository.update_run(session, run_row, derived_state=derived)
    session.commit()
    return snapshot


__all__ = [
    "RESEARCH_STATE_KEY",
    "ResearchRecoveryReport",
    "cancel_research_run",
    "load_frozen_requirement",
    "load_research_state",
    "rebuild_research_context",
    "recover_research_agent_run",
]
