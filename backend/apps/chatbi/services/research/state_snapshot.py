"""Research Agent 的规范状态快照和 Evidence DAG 校验。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from apps.chatbi.models.dto.research_agent import Evidence, ResearchStateSnapshot

if TYPE_CHECKING:
    from apps.chatbi.services.research.tool_context import ResearchToolContext

MAX_SNAPSHOT_JSON_CHARS = 400_000
RESEARCH_STATE_SNAPSHOT_KEY = "research_state_snapshot"


def build_research_state_snapshot(
    ctx: ResearchToolContext,
) -> ResearchStateSnapshot:
    """把当前 ResearchState 投影成唯一可恢复快照。"""

    state = ctx.research_state()
    raw_state = ctx.context.state.get("research_state")
    events = raw_state.get("state_events", []) if isinstance(raw_state, dict) else []
    if not isinstance(events, list):
        raise ValueError("RESEARCH_AGENT_STATE_EVENTS_INVALID")
    sequence = (
        raw_state.get("last_event_sequence", len(events))
        if isinstance(raw_state, dict)
        else len(events)
    )
    if not isinstance(sequence, int) or sequence < 0 or sequence != len(events):
        raise ValueError("RESEARCH_AGENT_STATE_EVENT_SEQUENCE_INVALID")
    snapshot = ResearchStateSnapshot(
        state=state,
        last_event_sequence=sequence,
        input_snapshot_ref=state.agent_input_ref,
    )
    ensure_snapshot_size(snapshot)
    return snapshot


def load_research_state_snapshot(
    derived_state: Mapping[str, object] | None,
) -> ResearchStateSnapshot:
    """严格加载新快照；旧快照只返回版本不支持错误。"""

    if not isinstance(derived_state, Mapping):
        raise ValueError("RESEARCH_AGENT_STATE_SNAPSHOT_MISSING")
    raw = derived_state.get(RESEARCH_STATE_SNAPSHOT_KEY)
    if raw is None:
        if "research_run_snapshot" in derived_state:
            raise ValueError("RESEARCH_AGENT_STATE_SNAPSHOT_VERSION_UNSUPPORTED")
        raise ValueError("RESEARCH_AGENT_STATE_SNAPSHOT_MISSING")
    if not isinstance(raw, Mapping):
        raise ValueError("RESEARCH_AGENT_STATE_SNAPSHOT_INVALID")
    if raw.get("schema_version") != ResearchStateSnapshot.SCHEMA_VERSION:
        raise ValueError("RESEARCH_AGENT_STATE_SNAPSHOT_VERSION_UNSUPPORTED")
    if raw.get("runtime_type") != "research_agent":
        raise ValueError("RESEARCH_AGENT_STATE_SNAPSHOT_RUNTIME_TYPE_INVALID")
    for field_name, error_code in (
        ("state", "RESEARCH_AGENT_STATE_SNAPSHOT_STATE_MISSING"),
        (
            "last_event_sequence",
            "RESEARCH_AGENT_STATE_SNAPSHOT_EVENT_SEQUENCE_MISSING",
        ),
        ("input_snapshot_ref", "RESEARCH_AGENT_STATE_SNAPSHOT_INPUT_REF_MISSING"),
    ):
        if field_name not in raw:
            raise ValueError(error_code)
    try:
        return ResearchStateSnapshot.model_validate(dict(raw))
    except ValueError as exc:
        if "RESEARCH_AGENT_SCHEMA_VERSION_UNSUPPORTED" in str(exc):
            raise ValueError(
                "RESEARCH_AGENT_STATE_SNAPSHOT_VERSION_UNSUPPORTED"
            ) from exc
        raise ValueError(f"RESEARCH_AGENT_STATE_SNAPSHOT_INVALID:{exc}") from exc


def validate_research_state_snapshot(
    snapshot: ResearchStateSnapshot,
    ctx: ResearchToolContext,
) -> None:
    """校验快照与恢复后的规范状态完全一致。"""

    if snapshot.state != ctx.research_state():
        raise ValueError("RESEARCH_AGENT_STATE_SNAPSHOT_STATE_MISMATCH")
    raw_state = ctx.context.state.get("research_state")
    if not isinstance(raw_state, dict):
        raise ValueError("RESEARCH_AGENT_RECOVERY_STATE_MISSING")
    events = raw_state.get("state_events", [])
    sequence = raw_state.get("last_event_sequence", len(events))
    if not isinstance(events, list) or sequence != snapshot.last_event_sequence:
        raise ValueError("RESEARCH_AGENT_STATE_SNAPSHOT_EVENT_SEQUENCE_MISMATCH")
    if raw_state.get("agent_input_ref") not in (
        None,
        snapshot.input_snapshot_ref,
    ):
        raise ValueError("RESEARCH_AGENT_STATE_SNAPSHOT_INPUT_REF_MISMATCH")


def validate_evidence_dag(
    evidences: Sequence[Evidence],
    *,
    run_id: str | None = None,
) -> None:
    """校验新 Evidence 的父子关系，防止缺失引用、自引用和循环。"""

    by_id: dict[str, Evidence] = {}
    for evidence in evidences:
        if evidence.evidence_id in by_id:
            raise ValueError("RESEARCH_AGENT_STATE_EVIDENCE_DUPLICATED")
        by_id[evidence.evidence_id] = evidence

    # 新 Evidence 不携带可伪造的 run_id，归属由 ResearchState 台账和结果映射保证。
    # 保留 run_id 参数是为了让调用方沿用统一校验入口；这里不把它写入 Evidence。
    del run_id
    for evidence in evidences:
        for parent_id in evidence.parent_evidence_ids:
            if parent_id not in by_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
            if parent_id == evidence.evidence_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_SELF_DEPENDENCY")

    indegree = dict.fromkeys(by_id, 0)
    dependents: dict[str, list[str]] = {evidence_id: [] for evidence_id in by_id}
    for evidence in evidences:
        for parent_id in evidence.parent_evidence_ids:
            indegree[evidence.evidence_id] += 1
            dependents[parent_id].append(evidence.evidence_id)
    frontier = [evidence_id for evidence_id, degree in indegree.items() if degree == 0]
    visited = 0
    while frontier:
        evidence_id = frontier.pop()
        visited += 1
        for dependent_id in dependents[evidence_id]:
            indegree[dependent_id] -= 1
            if indegree[dependent_id] == 0:
                frontier.append(dependent_id)
    if visited != len(by_id):
        raise ValueError("RESEARCH_AGENT_EVIDENCE_DEPENDENCY_CYCLE")


def ensure_snapshot_size(snapshot: ResearchStateSnapshot) -> int:
    """快照超过有界大小时拒绝持久化。"""

    encoded_chars = len(
        json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)
    )
    if encoded_chars > MAX_SNAPSHOT_JSON_CHARS:
        raise ValueError("RESEARCH_SNAPSHOT_TOO_LARGE")
    return encoded_chars


__all__ = [
    "MAX_SNAPSHOT_JSON_CHARS",
    "RESEARCH_STATE_SNAPSHOT_KEY",
    "ResearchStateSnapshot",
    "build_research_state_snapshot",
    "ensure_snapshot_size",
    "load_research_state_snapshot",
    "validate_evidence_dag",
    "validate_research_state_snapshot",
]
