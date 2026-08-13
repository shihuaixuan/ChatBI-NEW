"""ChatBI Agent Trace Node 的 SQLModel 持久化实现。"""

from __future__ import annotations

from typing import Any

import orjson
from sqlalchemy import delete, func
from sqlmodel import Session, col, select

from apps.chatbi.models.orm.agent_run import ChatbiAgentRun
from apps.chatbi.models.orm.agent_trace import ChatbiAgentTraceNode
from apps.trace import (
    TRACE_TERMINAL_STATUSES,
    TraceContractError,
    TraceNodeFinishInput,
    TraceNodeRef,
    TraceNodeStartInput,
    TraceNodeStatus,
    TraceNodeType,
    TraceRunFinishInput,
)

TRACE_SUMMARY_MAX_BYTES = 8 * 1024
TRACE_STATE_DIFF_MAX_BYTES = 16 * 1024


def ensure_run_root(
    session: Session,
    data: TraceNodeStartInput,
) -> tuple[ChatbiAgentTraceNode, bool]:
    """返回 Run 唯一根节点；不存在时在锁内创建。"""

    if data.parent_id is not None or data.node_type is not TraceNodeType.RUN:
        raise TraceContractError("TRACE_ROOT_CONTRACT_INVALID")
    _lock_run(session, data.run_id)
    existing = get_run_root(session, data.run_id)
    if existing is not None:
        return existing, False
    return _create_node_locked(session, data), True


def start_node(session: Session, data: TraceNodeStartInput) -> ChatbiAgentTraceNode:
    """创建子节点并校验父节点归属。"""

    if data.parent_id is None:
        raise TraceContractError("TRACE_CHILD_PARENT_REQUIRED")
    _lock_run(session, data.run_id)
    parent = session.get(ChatbiAgentTraceNode, data.parent_id)
    if parent is None:
        raise TraceContractError("TRACE_PARENT_NOT_FOUND")
    if parent.run_id != data.run_id:
        raise TraceContractError("TRACE_PARENT_RUN_MISMATCH")
    return _create_node_locked(session, data)


def finish_node(session: Session, data: TraceNodeFinishInput) -> None:
    """把运行中节点收敛到明确终态。"""

    if data.status not in TRACE_TERMINAL_STATUSES:
        raise TraceContractError("TRACE_TERMINAL_STATUS_REQUIRED")
    _validate_json_size("output_summary", data.output_summary, TRACE_SUMMARY_MAX_BYTES)
    _validate_json_size("state_diff", data.state_diff, TRACE_STATE_DIFF_MAX_BYTES)

    node = session.get(ChatbiAgentTraceNode, data.node_id)
    if node is None:
        raise TraceContractError("TRACE_NODE_NOT_FOUND")
    if node.status != TraceNodeStatus.RUNNING.value:
        raise TraceContractError("TRACE_NODE_ALREADY_FINISHED")

    node.status = data.status.value
    node.finished_at = data.finished_at
    node.latency_ms = max(
        0,
        int((data.finished_at - node.started_at).total_seconds() * 1000),
    )
    node.output_summary = data.output_summary
    node.input_artifact_ref = data.input_artifact_ref
    node.output_artifact_ref = data.output_artifact_ref
    node.state_diff = data.state_diff
    node.token_usage = data.token_usage
    node.error_code = data.error_code
    node.error_category = data.error_category
    node.error = data.error
    node.trace_id = data.trace_id
    node.span_id = data.span_id
    node.metadata_json = {**node.metadata_json, **data.metadata}
    session.add(node)


def finish_run_root(session: Session, data: TraceRunFinishInput) -> None:
    """收口 Run 根节点；已经标记 partial 时保留不完整状态。"""

    if data.status in {TraceNodeStatus.RUNNING, TraceNodeStatus.WAITING}:
        raise TraceContractError("TRACE_RUN_TERMINAL_STATUS_REQUIRED")
    _validate_json_size("output_summary", data.output_summary, TRACE_SUMMARY_MAX_BYTES)
    _lock_run(session, data.run_id)
    root = get_run_root(session, data.run_id)
    if root is None:
        raise TraceContractError("TRACE_ROOT_NOT_FOUND")
    if root.status == TraceNodeStatus.PARTIAL.value:
        root.finished_at = data.finished_at
        root.latency_ms = max(
            0,
            int((data.finished_at - root.started_at).total_seconds() * 1000),
        )
        root.output_summary = data.output_summary
        root.error_code = data.error_code
        root.error_category = data.error_category
        root.error = data.error
        root.metadata_json = {
            **root.metadata_json,
            "business_terminal_status": data.status.value,
        }
        session.add(root)
        return
    if root.status == data.status.value:
        return
    if root.status != TraceNodeStatus.RUNNING.value:
        raise TraceContractError("TRACE_ROOT_ALREADY_FINISHED")
    if root.id is None:
        raise TraceContractError("TRACE_ROOT_ID_REQUIRED")
    finish_node(
        session,
        TraceNodeFinishInput(
            node_id=root.id,
            status=data.status,
            finished_at=data.finished_at,
            output_summary=data.output_summary,
            error_code=data.error_code,
            error_category=data.error_category,
            error=data.error,
        ),
    )


def mark_run_partial(session: Session, run_id: int, *, lost_nodes: int = 1) -> None:
    """标记调用树存在丢失节点，并保留此前业务终态。"""

    if lost_nodes <= 0:
        raise TraceContractError("TRACE_LOST_NODES_INVALID")
    _lock_run(session, run_id)
    root = get_run_root(session, run_id)
    if root is None:
        raise TraceContractError("TRACE_ROOT_NOT_FOUND")
    metadata = dict(root.metadata_json)
    metadata.setdefault("business_status_before_partial", root.status)
    metadata["lost_nodes"] = int(metadata.get("lost_nodes") or 0) + lost_nodes
    root.status = TraceNodeStatus.PARTIAL.value
    root.metadata_json = metadata
    session.add(root)


def get_run_root(session: Session, run_id: int) -> ChatbiAgentTraceNode | None:
    stmt = select(ChatbiAgentTraceNode).where(
        col(ChatbiAgentTraceNode.run_id) == run_id,
        col(ChatbiAgentTraceNode.parent_id).is_(None),
    )
    return session.exec(stmt).first()


def list_run_nodes(session: Session, run_id: int) -> list[ChatbiAgentTraceNode]:
    stmt = (
        select(ChatbiAgentTraceNode)
        .where(col(ChatbiAgentTraceNode.run_id) == run_id)
        .order_by(col(ChatbiAgentTraceNode.sequence))
    )
    return list(session.exec(stmt).all())


def get_run_node(
    session: Session,
    run_id: int,
    node_id: int,
) -> ChatbiAgentTraceNode | None:
    """按 Run 与节点联合定位，避免跨 Run 读取节点详情。"""

    stmt = select(ChatbiAgentTraceNode).where(
        col(ChatbiAgentTraceNode.run_id) == run_id,
        col(ChatbiAgentTraceNode.id) == node_id,
    )
    return session.exec(stmt).first()


def delete_nodes_for_runs(session: Session, run_ids: list[int]) -> None:
    """删除一组 Run 的全部 Trace 节点。"""

    if not run_ids:
        return
    session.execute(
        delete(ChatbiAgentTraceNode).where(
            col(ChatbiAgentTraceNode.run_id).in_(run_ids)
        )
    )


def to_ref(node: ChatbiAgentTraceNode) -> TraceNodeRef:
    if node.id is None:
        raise TraceContractError("TRACE_NODE_ID_REQUIRED")
    return TraceNodeRef(
        id=node.id,
        run_id=node.run_id,
        parent_id=node.parent_id,
        node_key=node.node_key,
        sequence=node.sequence,
        node_type=TraceNodeType(node.node_type),
    )


def _create_node_locked(
    session: Session,
    data: TraceNodeStartInput,
) -> ChatbiAgentTraceNode:
    _validate_json_size("input_summary", data.input_summary, TRACE_SUMMARY_MAX_BYTES)
    sequence = _next_sequence_locked(session, data.run_id)
    node = ChatbiAgentTraceNode(
        run_id=data.run_id,
        parent_id=data.parent_id,
        node_key=data.node_key or f"{data.name}:{sequence}",
        node_type=data.node_type.value,
        name=data.name,
        display_name=data.display_name,
        status=TraceNodeStatus.RUNNING.value,
        sequence=sequence,
        started_at=data.started_at,
        input_summary=data.input_summary,
        input_artifact_ref=data.input_artifact_ref,
        metadata_json=data.metadata,
    )
    session.add(node)
    session.flush()
    session.refresh(node)
    return node


def _lock_run(session: Session, run_id: int) -> None:
    """串行化同一 Run 的节点序号分配。"""

    run = session.exec(
        select(ChatbiAgentRun)
        .where(col(ChatbiAgentRun.id) == run_id)
        .with_for_update()
    ).first()
    if run is None:
        raise TraceContractError("TRACE_RUN_NOT_FOUND")


def _next_sequence_locked(session: Session, run_id: int) -> int:
    current = session.exec(
        select(func.max(col(ChatbiAgentTraceNode.sequence))).where(
            col(ChatbiAgentTraceNode.run_id) == run_id
        )
    ).one()
    return int(current or 0) + 1


def _validate_json_size(name: str, payload: dict[str, Any], maximum: int) -> None:
    try:
        size = len(orjson.dumps(payload))
    except (TypeError, ValueError) as exc:
        raise TraceContractError(f"TRACE_{name.upper()}_NOT_JSON") from exc
    if size > maximum:
        raise TraceContractError(f"TRACE_{name.upper()}_TOO_LARGE")


__all__ = [
    "TRACE_STATE_DIFF_MAX_BYTES",
    "TRACE_SUMMARY_MAX_BYTES",
    "delete_nodes_for_runs",
    "ensure_run_root",
    "finish_node",
    "finish_run_root",
    "get_run_root",
    "get_run_node",
    "list_run_nodes",
    "mark_run_partial",
    "start_node",
    "to_ref",
]
