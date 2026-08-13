"""持久化 Agent Trace 的公共数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TraceNodeType(str, Enum):
    """调用树节点的稳定类型。"""

    RUN = "run"
    INVOCATION = "invocation"
    PHASE = "phase"
    LLM = "llm"
    TOOL = "tool"
    VALIDATION = "validation"
    PROJECTION = "projection"
    PERSISTENCE = "persistence"
    INTERACTION = "interaction"


class TraceNodeStatus(str, Enum):
    """调用树节点的稳定状态。"""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    INTERRUPTED = "interrupted"
    WAITING = "waiting"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    PARTIAL = "partial"


TRACE_TERMINAL_STATUSES = frozenset(
    status for status in TraceNodeStatus if status is not TraceNodeStatus.RUNNING
)


@dataclass(frozen=True)
class TraceNodeSpec:
    """业务调用方声明的节点身份和展示信息。"""

    run_id: int
    node_type: TraceNodeType
    name: str
    display_name: str
    node_key: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceNodeStartInput:
    """Trace Store 创建节点所需的数据。"""

    run_id: int
    parent_id: int | None
    node_type: TraceNodeType
    name: str
    display_name: str
    started_at: datetime
    node_key: str | None = None
    input_summary: dict[str, Any] = field(default_factory=dict)
    input_artifact_ref: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceNodeFinishInput:
    """Trace Store 收口节点所需的数据。"""

    node_id: int
    status: TraceNodeStatus
    finished_at: datetime
    output_summary: dict[str, Any] = field(default_factory=dict)
    input_artifact_ref: dict[str, Any] | None = None
    output_artifact_ref: dict[str, Any] | None = None
    state_diff: dict[str, Any] = field(default_factory=dict)
    token_usage: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_category: str | None = None
    error: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceRunFinishInput:
    """Trace Store 收口 Run 根节点所需的数据。"""

    run_id: int
    status: TraceNodeStatus
    finished_at: datetime
    output_summary: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_category: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class TraceNodeRef:
    """Recorder 传播父子上下文使用的最小节点引用。"""

    id: int
    run_id: int
    parent_id: int | None
    node_key: str
    sequence: int
    node_type: TraceNodeType


@dataclass(frozen=True)
class TraceDetailWriteInput:
    """单个节点脱敏详情的 Artifact 写入请求。"""

    run_id: int
    node_id: int
    chat_id: int
    record_id: int
    side: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "TRACE_TERMINAL_STATUSES",
    "TraceDetailWriteInput",
    "TraceNodeFinishInput",
    "TraceNodeRef",
    "TraceNodeSpec",
    "TraceNodeStartInput",
    "TraceNodeStatus",
    "TraceNodeType",
    "TraceRunFinishInput",
]
