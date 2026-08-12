"""把 Agent Trace 持久化事实投影为执行详情查询结果。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apps.chatbi.models import (
    AgentRunStatus,
    AgentTraceNodeDetailSnapshot,
    AgentTraceNodeSnapshot,
    AgentTraceOverviewSnapshot,
    AgentTraceSnapshot,
    ChatbiAgentRun,
    ChatbiAgentTraceNode,
    ResultArtifactReadInput,
)
from apps.chatbi.services.execution.result_artifacts import ResultArtifactService
from apps.conversation import ChatRecordExecutionType
from apps.trace import TraceNodeStatus, TraceNodeType

_RUN_STATUS_TO_TRACE_STATUS = {
    AgentRunStatus.CREATED.value: TraceNodeStatus.RUNNING.value,
    AgentRunStatus.RUNNING.value: TraceNodeStatus.RUNNING.value,
    AgentRunStatus.CANCEL_REQUESTED.value: TraceNodeStatus.RUNNING.value,
    AgentRunStatus.WAITING_USER.value: TraceNodeStatus.WAITING.value,
    AgentRunStatus.FINISHED.value: TraceNodeStatus.SUCCEEDED.value,
    AgentRunStatus.FAILED.value: TraceNodeStatus.FAILED.value,
    AgentRunStatus.CANCELLED.value: TraceNodeStatus.CANCELLED.value,
}
_TERMINAL_RUN_STATUSES = {
    AgentRunStatus.FINISHED.value,
    AgentRunStatus.FAILED.value,
    AgentRunStatus.CANCELLED.value,
}


def project_agent_trace(
    run: ChatbiAgentRun,
    nodes: list[ChatbiAgentTraceNode],
    *,
    observed_at: datetime | None = None,
) -> AgentTraceSnapshot:
    """生成调用树；Run 根节点状态以业务 Run 终态为准。"""

    if run.id is None:
        raise ValueError("AGENT_TRACE_RUN_ID_REQUIRED")

    ordered_nodes = sorted(nodes, key=lambda item: item.sequence)
    root = next((item for item in ordered_nodes if item.parent_id is None), None)
    started_at = root.started_at if root is not None else run.created_at
    latest_finished_at = max(
        (item.finished_at for item in ordered_nodes if item.finished_at is not None),
        default=None,
    )
    terminal = run.status in _TERMINAL_RUN_STATUSES
    finished_at = latest_finished_at if terminal else None
    duration_end = finished_at or observed_at or datetime.now()
    duration_ms = (
        max(0, int((duration_end - started_at).total_seconds() * 1000))
        if started_at is not None
        else 0
    )

    input_tokens, output_tokens, total_tokens = _sum_llm_tokens(ordered_nodes)
    root_status = _project_root_status(run, root)
    tree = _build_tree(ordered_nodes, root_status, finished_at)
    statuses = [
        root_status if item is root else item.status for item in ordered_nodes
    ]
    partial = bool(
        root is not None
        and (
            root.status == TraceNodeStatus.PARTIAL.value
            or int(root.metadata_json.get("lost_nodes") or 0) > 0
        )
    )
    overview = AgentTraceOverviewSnapshot(
        run_id=run.id,
        record_id=run.record_id,
        status=root_status,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        node_count=len(ordered_nodes),
        failed_node_count=sum(status == TraceNodeStatus.FAILED.value for status in statuses),
        waiting_node_count=sum(status == TraceNodeStatus.WAITING.value for status in statuses),
        last_sequence=max((item.sequence for item in ordered_nodes), default=0),
        partial=partial,
    )
    return AgentTraceSnapshot(overview=overview, tree=tree)


def project_agent_trace_node_detail(
    node: ChatbiAgentTraceNode,
    *,
    input_detail: dict[str, Any] | None,
    output_detail: dict[str, Any] | None,
) -> AgentTraceNodeDetailSnapshot:
    """生成一个节点的可调试详情。"""

    return AgentTraceNodeDetailSnapshot(
        node=_project_node(node),
        input_summary=node.input_summary,
        output_summary=node.output_summary,
        input_detail=input_detail,
        output_detail=output_detail,
        state_diff=node.state_diff,
        metadata=node.metadata_json,
        trace_id=node.trace_id,
        span_id=node.span_id,
    )


def load_agent_trace_node_detail(
    run: ChatbiAgentRun,
    node: ChatbiAgentTraceNode,
    artifact_service: ResultArtifactService,
) -> AgentTraceNodeDetailSnapshot:
    """按节点引用读取完整详情，Artifact 归属校验集中在统一 Service。"""

    run_id = run.id
    node_id = node.id
    if run_id is None or node_id is None:
        raise ValueError("AGENT_TRACE_ID_REQUIRED")

    details: dict[str, dict[str, Any] | None] = {"input": None, "output": None}
    for side, reference in (
        ("input", node.input_artifact_ref),
        ("output", node.output_artifact_ref),
    ):
        if reference is None:
            continue
        artifact_id = reference.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("AGENT_TRACE_ARTIFACT_REFERENCE_INVALID")
        snapshot = artifact_service.read(
            ResultArtifactReadInput(
                artifact_id=artifact_id,
                execution_id=f"agent:{run_id}",
                execution_type=ChatRecordExecutionType.AGENT,
                chat_id=run.chat_id,
                record_id=run.record_id,
                kind=f"agent_trace_{side}",
                expected_metadata={
                    "run_id": run_id,
                    "node_id": node_id,
                    "side": side,
                },
            )
        )
        details[side] = snapshot.payload

    return project_agent_trace_node_detail(
        node,
        input_detail=details["input"],
        output_detail=details["output"],
    )


def _build_tree(
    nodes: list[ChatbiAgentTraceNode],
    root_status: str,
    root_finished_at: datetime | None,
) -> list[AgentTraceNodeSnapshot]:
    projected: dict[int, AgentTraceNodeSnapshot] = {}
    source_by_id: dict[int, ChatbiAgentTraceNode] = {}
    for node in nodes:
        if node.id is None:
            continue
        is_root = node.parent_id is None
        projected[node.id] = _project_node(
            node,
            status=root_status if is_root else None,
            finished_at=root_finished_at if is_root else None,
        )
        source_by_id[node.id] = node

    roots: list[AgentTraceNodeSnapshot] = []
    for node_id, item in projected.items():
        source = source_by_id[node_id]
        parent = projected.get(source.parent_id) if source.parent_id is not None else None
        if parent is None:
            roots.append(item)
        else:
            parent.children.append(item)
    return roots


def _project_node(
    node: ChatbiAgentTraceNode,
    *,
    status: str | None = None,
    finished_at: datetime | None = None,
) -> AgentTraceNodeSnapshot:
    if node.id is None:
        raise ValueError("AGENT_TRACE_NODE_ID_REQUIRED")
    effective_finished_at = finished_at or node.finished_at
    effective_latency = node.latency_ms
    if effective_latency is None and effective_finished_at is not None:
        effective_latency = max(
            0,
            int((effective_finished_at - node.started_at).total_seconds() * 1000),
        )
    return AgentTraceNodeSnapshot(
        id=node.id,
        parent_id=node.parent_id,
        node_key=node.node_key,
        node_type=node.node_type,
        name=node.name,
        display_name=node.display_name,
        status=status or node.status,
        sequence=node.sequence,
        started_at=node.started_at,
        finished_at=effective_finished_at,
        latency_ms=effective_latency,
        token_usage=node.token_usage,
        error_code=node.error_code,
        error_category=node.error_category,
        error=node.error,
        has_input_detail=node.input_artifact_ref is not None,
        has_output_detail=node.output_artifact_ref is not None,
    )


def _project_root_status(
    run: ChatbiAgentRun,
    root: ChatbiAgentTraceNode | None,
) -> str:
    if root is not None and root.status == TraceNodeStatus.PARTIAL.value:
        return TraceNodeStatus.PARTIAL.value
    return _RUN_STATUS_TO_TRACE_STATUS.get(run.status, TraceNodeStatus.RUNNING.value)


def _sum_llm_tokens(
    nodes: list[ChatbiAgentTraceNode],
) -> tuple[int, int, int]:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    for node in nodes:
        if node.node_type != TraceNodeType.LLM.value:
            continue
        usage = node.token_usage
        node_input = _non_negative_int(
            usage.get("input_tokens", usage.get("prompt_tokens", 0))
        )
        node_output = _non_negative_int(
            usage.get("output_tokens", usage.get("completion_tokens", 0))
        )
        node_total = _non_negative_int(usage.get("total_tokens", 0))
        input_tokens += node_input
        output_tokens += node_output
        total_tokens += node_total or node_input + node_output
    return input_tokens, output_tokens, total_tokens


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


__all__ = [
    "load_agent_trace_node_detail",
    "project_agent_trace",
    "project_agent_trace_node_detail",
]
