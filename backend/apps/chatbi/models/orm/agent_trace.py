"""ChatBI Agent 持久化调用树节点。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from apps.trace import TraceNodeStatus, TraceNodeType


class ChatbiAgentTraceNode(SQLModel, table=True):
    """一个可独立查看输入输出的 Agent 执行节点。"""

    __tablename__ = "chatbi_agent_trace_node"
    __table_args__ = (
        Index("ux_chatbi_agent_trace_node_key", "run_id", "node_key", unique=True),
        Index("ux_chatbi_agent_trace_sequence", "run_id", "sequence", unique=True),
        Index(
            "ux_chatbi_agent_trace_root",
            "run_id",
            unique=True,
            postgresql_where=text("parent_id IS NULL"),
        ),
        Index(
            "idx_chatbi_agent_trace_parent",
            "run_id",
            "parent_id",
            "sequence",
        ),
        Index("idx_chatbi_agent_trace_started", "run_id", "started_at"),
    )

    id: int | None = Field(
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True)
    )
    run_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    parent_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    node_key: str = Field(max_length=160, nullable=False)
    node_type: str = Field(
        default=TraceNodeType.PHASE.value,
        max_length=32,
        nullable=False,
    )
    name: str = Field(max_length=128, nullable=False)
    display_name: str = Field(max_length=160, nullable=False)
    status: str = Field(
        default=TraceNodeStatus.RUNNING.value,
        max_length=32,
        nullable=False,
    )
    sequence: int = Field(sa_column=Column(Integer, nullable=False))
    started_at: datetime = Field(sa_column=Column(DateTime(timezone=False), nullable=False))
    finished_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )
    latency_ms: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
    input_summary: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    output_summary: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    input_artifact_ref: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    output_artifact_ref: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    state_diff: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    token_usage: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    error_code: str | None = Field(default=None, max_length=128, nullable=True)
    error_category: str | None = Field(default=None, max_length=64, nullable=True)
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    trace_id: str | None = Field(default=None, max_length=64, nullable=True)
    span_id: str | None = Field(default=None, max_length=32, nullable=True)
    metadata_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            "metadata",
            JSONB,
            nullable=False,
            server_default=text("'{}'::jsonb"),
        ),
    )


__all__ = ["ChatbiAgentTraceNode"]
