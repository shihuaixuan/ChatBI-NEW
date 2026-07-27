"""Agent 执行记录、步骤、轨迹与澄清的持久化模型。"""

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


class AgentRunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStepStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class AgentErrorClass(str, Enum):
    UNDERSTANDING = "understanding_failed"
    RETRIEVAL = "retrieval_missed"
    SQL = "sql_failed"
    BUDGET = "budget_exhausted"
    UNEXPECTED = "unexpected_error"


class AgentClarificationStatus(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AgentClarificationResumeKind(str, Enum):
    """澄清回答应回填到的确定性恢复边界。"""

    QUESTION_UNDERSTANDING = "question_understanding"
    AGENT_TOOL = "agent_tool"


class ChatbiAgentRun(SQLModel, table=True):
    __tablename__ = "chatbi_agent_run"
    __table_args__ = (
        Index("idx_chatbi_agent_run_record", "record_id"),
        Index("idx_chatbi_agent_run_chat", "chat_id", text("created_at DESC")),
        Index("idx_chatbi_agent_run_status", "oid", "status", text("updated_at DESC")),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    chat_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    record_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    status: str = Field(default=AgentRunStatus.CREATED.value, max_length=32, nullable=False)
    # Agent 原生消息历史（不含 system）；工作流前置澄清保存在 clarification 与 derived_state 中。
    messages: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    budget_snapshot: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    # 工具执行的派生状态（语义资产集合、白名单表、最近执行摘要等，不含全量数据），
    # 澄清挂起后恢复时回填 AgentToolContext.state，避免恢复后被迫重新检索。
    derived_state: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    config: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    error_class: str | None = Field(default=None, max_length=64, nullable=True)
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    created_by: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))


class ChatbiAgentStep(SQLModel, table=True):
    __tablename__ = "chatbi_agent_step"
    __table_args__ = (
        Index("ux_chatbi_agent_step_index", "run_id", "step_index", unique=True),
        Index("idx_chatbi_agent_step_run", "run_id", "created_at"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    run_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    step_index: int = Field(sa_column=Column(Integer, nullable=False))
    tool_name: str | None = Field(default=None, max_length=128, nullable=True)
    args_summary: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    result_summary: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    status: str = Field(default=AgentStepStatus.RUNNING.value, max_length=32, nullable=False)
    latency_ms: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    token_usage: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class ChatbiAgentClarification(SQLModel, table=True):
    __tablename__ = "chatbi_agent_clarification"
    __table_args__ = (
        Index("idx_chatbi_agent_clarification_record", "record_id", "status"),
        Index(
            "ux_chatbi_agent_clarification_pending",
            "run_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    run_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    record_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    status: str = Field(default=AgentClarificationStatus.PENDING.value, max_length=32, nullable=False)
    # 模型主动澄清时保存工具调用 id；问题理解澄清不进入工具消息协议。
    tool_call_id: str | None = Field(default=None, max_length=128, nullable=True)
    resume_kind: str = Field(max_length=64, nullable=False)
    resume_payload: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    question: str = Field(sa_column=Column(Text, nullable=False))
    options: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    answer: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    answered_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    created_by: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
