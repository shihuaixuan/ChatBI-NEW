"""通用事件应用管理的 Event log 持久化模型。"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Identity, Index, Integer, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class EventLog(SQLModel, table=True):
    """有序产品事件。

    阶段 1 继续复用原表和 run_id 字段，避免同时引入数据库迁移。
    """

    __tablename__ = "chatbi_agent_trace_event"
    __table_args__ = (
        Index("ux_chatbi_agent_trace_sequence", "run_id", "sequence", unique=True),
    )

    id: int | None = Field(
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True)
    )
    run_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    step_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    # run 内单调递增，供 after_sequence 断线补拉。
    sequence: int = Field(sa_column=Column(Integer, nullable=False))
    event_type: str = Field(max_length=64, nullable=False)
    payload: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )


# 兼容旧模型名称；新代码统一使用 EventLog。
ChatbiAgentTraceEvent = EventLog

__all__ = ["ChatbiAgentTraceEvent", "EventLog"]
