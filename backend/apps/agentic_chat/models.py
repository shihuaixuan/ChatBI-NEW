from datetime import datetime
from enum import Enum

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


class AgenticRunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgenticStepStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgenticClarificationStatus(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AgenticRun(SQLModel, table=True):
    __tablename__ = "agentic_run"
    __table_args__ = (
        Index("idx_agentic_run_record", "record_id"),
        Index("idx_agentic_run_chat", "chat_id", text("created_at DESC")),
        Index("idx_agentic_run_status", "oid", "status", text("updated_at DESC")),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    chat_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    record_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    status: str = Field(default=AgenticRunStatus.CREATED.value, max_length=32, nullable=False)
    mode: str = Field(default="agentic_chatbi", max_length=32, nullable=False)
    current_step: str | None = Field(default=None, max_length=64, nullable=True)
    # 保存 AgenticState 的可恢复快照，不保存完整 SQL 执行结果。
    state: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    config: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    created_by: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))


class AgenticStep(SQLModel, table=True):
    __tablename__ = "agentic_step"
    __table_args__ = (
        Index("ux_agentic_step_index", "run_id", "step_index", unique=True),
        Index("idx_agentic_step_run", "run_id", "created_at"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    run_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    step_index: int = Field(sa_column=Column(Integer, nullable=False))
    step_type: str = Field(max_length=64, nullable=False)
    tool_name: str | None = Field(default=None, max_length=128, nullable=True)
    strategy: str | None = Field(default=None, max_length=64, nullable=True)
    # 仅记录入参摘要，避免敏感数据和长上下文进入 step 表。
    input_summary: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    output_summary: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    status: str = Field(default=AgenticStepStatus.RUNNING.value, max_length=32, nullable=False)
    duration_ms: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    token_usage: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class AgenticClarification(SQLModel, table=True):
    __tablename__ = "agentic_clarification"
    __table_args__ = (
        Index("idx_agentic_clarification_run", "run_id", text("created_at DESC")),
        Index("idx_agentic_clarification_record", "record_id", "status"),
        Index("ux_agentic_clarification_pending", "run_id", unique=True, postgresql_where=text("status = 'pending'")),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    run_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    record_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    status: str = Field(default=AgenticClarificationStatus.PENDING.value, max_length=32, nullable=False)
    target_slots: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    question: str = Field(sa_column=Column(Text, nullable=False))
    options: list[dict] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    answer: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    answered_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    created_by: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))


class AgenticTraceEvent(SQLModel, table=True):
    __tablename__ = "agentic_trace_event"
    __table_args__ = (Index("idx_agentic_trace_event_run", "run_id", "created_at"),)

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    run_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    step_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    event_type: str = Field(max_length=64, nullable=False)
    public_payload: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    # 私有载荷只放调试摘要，后续可按保留策略清理。
    private_payload: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
