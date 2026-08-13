"""Agent 执行详情查询契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentTraceOverviewSnapshot(BaseModel):
    """一次 Agent Run 的执行概览。"""

    run_id: int
    record_id: int
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    node_count: int = Field(ge=0)
    llm_call_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    invocation_count: int = Field(ge=0)
    recovery_count: int = Field(ge=0)
    failed_node_count: int = Field(ge=0)
    waiting_node_count: int = Field(ge=0)
    last_sequence: int = Field(ge=0)
    partial: bool = False
    trace_complete: bool = False


class AgentTraceNodeSnapshot(BaseModel):
    """调用树中的扁平轻量节点，不携带 Artifact 正文。"""

    id: int
    parent_id: int | None = None
    node_key: str
    node_type: str
    name: str
    display_name: str
    status: str
    sequence: int
    started_at: datetime
    finished_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    token_usage: dict[str, Any] = Field(default_factory=dict)
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_category: str | None = None
    error: str | None = None
    has_input_detail: bool = False
    has_output_detail: bool = False


class AgentTraceSnapshot(BaseModel):
    """执行概览与按 sequence 排序的扁平节点。"""

    available: bool = True
    unavailable_reason: str | None = None
    detail_access: Literal["allowed", "summary_only"] = "summary_only"
    overview: AgentTraceOverviewSnapshot
    nodes: list[AgentTraceNodeSnapshot] = Field(default_factory=list)


class AgentTraceNodeDetailSnapshot(BaseModel):
    """单个节点的摘要、状态变化及完整脱敏输入输出。"""

    node: AgentTraceNodeSnapshot
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    input_detail: dict[str, Any] | None = None
    output_detail: dict[str, Any] | None = None
    state_diff: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    span_id: str | None = None


__all__ = [
    "AgentTraceNodeDetailSnapshot",
    "AgentTraceNodeSnapshot",
    "AgentTraceOverviewSnapshot",
    "AgentTraceSnapshot",
]
