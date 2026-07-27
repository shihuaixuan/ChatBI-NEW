"""通用事件应用管理的 Event log 持久化模型。"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Identity, Index, Integer, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class EventLog(SQLModel, table=True):
    """有序产品事件。

    阶段 6 评估后继续复用原表和 run_id 字段；代码边界通用化不等于物理表合并。
    """

    __tablename__ = "chatbi_agent_event"
    __table_args__ = (
        Index("ux_chatbi_agent_event_sequence", "run_id", "sequence", unique=True),
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
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )

__all__ = ["EventLog"]
