"""ResearchState 的 Finding、Todo 变更校验和事件重建。"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from apps.chatbi.models.dto.research_agent import (
    Finding,
    FindingChange,
    TodoChange,
    TodoItem,
)


def apply_finding_changes(
    findings: Sequence[Finding],
    changes: Sequence[FindingChange | Mapping[str, Any]],
    *,
    evidence_ids: Collection[str],
) -> tuple[Finding, ...]:
    """在内存副本上校验并应用 FindingChange，不修改输入集合。"""

    staged = list(findings)
    known_evidence_ids = set(evidence_ids)
    for raw_change in changes:
        change = _finding_change(raw_change)
        if change.change_type == "add":
            assert change.finding is not None
            finding = change.finding
            if finding.status != "confirmed":
                raise ValueError("RESEARCH_AGENT_FINDING_ADD_STATUS_INVALID")
            if any(item.finding_id == finding.finding_id for item in staged):
                raise ValueError("RESEARCH_AGENT_FINDING_DUPLICATED")
            missing = set(finding.evidence_ids) - known_evidence_ids
            if missing:
                raise ValueError("RESEARCH_AGENT_FINDING_EVIDENCE_NOT_FOUND")
            staged.append(finding)
            continue

        assert change.finding_id is not None
        index = _finding_index(staged, change.finding_id)
        if index is None:
            raise ValueError("RESEARCH_AGENT_FINDING_SUPERSEDE_NOT_FOUND")
        if staged[index].status != "confirmed":
            raise ValueError("RESEARCH_AGENT_FINDING_SUPERSEDE_NOT_CONFIRMED")
        staged[index] = staged[index].model_copy(update={"status": "superseded"})
    return tuple(staged)


def apply_todo_changes(
    todos: Sequence[TodoItem],
    changes: Sequence[TodoChange | Mapping[str, Any]],
    *,
    evidence_ids: Collection[str],
) -> tuple[TodoItem, ...]:
    """在内存副本上校验并应用 TodoChange，不修改输入集合。"""

    staged = list(todos)
    known_evidence_ids = set(evidence_ids)
    for raw_change in changes:
        change = _todo_change(raw_change)
        if change.change_type == "add":
            assert change.todo is not None
            todo = change.todo
            if any(item.todo_id == todo.todo_id for item in staged):
                raise ValueError("RESEARCH_AGENT_TODO_DUPLICATED")
            if not set(todo.related_evidence_ids) <= known_evidence_ids:
                raise ValueError("RESEARCH_AGENT_TODO_EVIDENCE_NOT_FOUND")
            staged.append(todo)
            continue

        assert change.todo_id is not None
        index = _todo_index(staged, change.todo_id)
        if index is None:
            raise ValueError("RESEARCH_AGENT_TODO_NOT_FOUND")
        current = staged[index]
        if change.change_type == "set_order":
            assert change.order is not None
            staged[index] = current.model_copy(update={"order": change.order})
            continue

        assert change.status is not None
        if not _todo_status_transition_allowed(current.status, change.status):
            raise ValueError("RESEARCH_AGENT_TODO_STATUS_TRANSITION_INVALID")
        result_reason = (
            change.result_reason
            if change.status in {"completed", "skipped"}
            else None
        )
        staged[index] = current.model_copy(
            update={
                "status": change.status,
                "result_reason": result_reason,
            }
        )
    return tuple(staged)


def replay_state_events(
    events: Sequence[Mapping[str, Any]],
    *,
    evidence_ids: Collection[str],
) -> tuple[tuple[Finding, ...], tuple[TodoItem, ...]]:
    """按严格连续的事件序号重建 Finding 和 Todo 当前状态。"""

    findings: tuple[Finding, ...] = ()
    todos: tuple[TodoItem, ...] = ()
    expected_sequence = 1
    for event in events:
        sequence = event.get("event_sequence")
        if sequence != expected_sequence:
            raise ValueError("RESEARCH_AGENT_STATE_EVENT_SEQUENCE_INVALID")
        event_type = event.get("event_type")
        change = event.get("change")
        if not isinstance(change, Mapping):
            raise ValueError("RESEARCH_AGENT_STATE_EVENT_INVALID")
        if event_type == "finding_change":
            findings = apply_finding_changes(
                findings,
                (change,),
                evidence_ids=evidence_ids,
            )
        elif event_type == "todo_change":
            todos = apply_todo_changes(
                todos,
                (change,),
                evidence_ids=evidence_ids,
            )
        else:
            raise ValueError("RESEARCH_AGENT_STATE_EVENT_TYPE_INVALID")
        expected_sequence += 1
    return findings, todos


def validate_event_sequence(events: Sequence[Mapping[str, Any]]) -> None:
    """校验事件序号连续，防止状态提交覆盖或遗漏历史事件。"""

    expected_sequence = 1
    for event in events:
        if event.get("event_sequence") != expected_sequence:
            raise ValueError("RESEARCH_AGENT_STATE_EVENT_SEQUENCE_INVALID")
        expected_sequence += 1


def _finding_change(
    change: FindingChange | Mapping[str, Any],
) -> FindingChange:
    if isinstance(change, FindingChange):
        return change
    return FindingChange.model_validate(change)


def _todo_change(todo: TodoChange | Mapping[str, Any]) -> TodoChange:
    if isinstance(todo, TodoChange):
        return todo
    return TodoChange.model_validate(todo)


def _finding_index(findings: Sequence[Finding], finding_id: str) -> int | None:
    for index, finding in enumerate(findings):
        if finding.finding_id == finding_id:
            return index
    return None


def _todo_index(todos: Sequence[TodoItem], todo_id: str) -> int | None:
    for index, todo in enumerate(todos):
        if todo.todo_id == todo_id:
            return index
    return None


def _todo_status_transition_allowed(
    current: str,
    target: str,
) -> bool:
    """集中定义 Todo 状态转换；完成和跳过后不允许回退。"""

    transitions = {
        "pending": {"pending", "in_progress", "completed", "skipped"},
        "in_progress": {"pending", "in_progress", "completed", "skipped"},
        "completed": {"completed"},
        "skipped": {"skipped"},
    }
    return target in transitions.get(current, set())


__all__ = [
    "apply_finding_changes",
    "apply_todo_changes",
    "replay_state_events",
    "validate_event_sequence",
]
