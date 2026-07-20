"""会话 ORM：chat_record 表。"""

from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Identity, Text
from sqlmodel import Field, SQLModel


class ChatFinishStep(Enum):
    GENERATE_SQL = 1
    QUERY_DATA = 2
    GENERATE_CHART = 3


class ChatRecord(SQLModel, table=True):
    __tablename__ = "chat_record"

    id: int | None = Field(
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True)
    )
    chat_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    ai_modal_id: int | None = Field(sa_column=Column(BigInteger))
    first_chat: bool = Field(
        sa_column=Column(Boolean, nullable=True, default=False)
    )
    create_time: datetime = Field(
        sa_column=Column(DateTime(timezone=False), nullable=True)
    )
    finish_time: datetime | None = Field(
        sa_column=Column(DateTime(timezone=False), nullable=True)
    )
    create_by: int = Field(sa_column=Column(BigInteger, nullable=True))
    dataset_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    datasource: int | None = Field(sa_column=Column(BigInteger, nullable=True))
    engine_type: str | None = Field(max_length=64, nullable=True)
    question: str | None = Field(sa_column=Column(Text, nullable=True))
    sql_answer: str | None = Field(sa_column=Column(Text, nullable=True))
    sql: str | None = Field(sa_column=Column(Text, nullable=True))
    sql_exec_result: str | None = Field(sa_column=Column(Text, nullable=True))
    data: str | None = Field(sa_column=Column(Text, nullable=True))
    chart_answer: str | None = Field(sa_column=Column(Text, nullable=True))
    chart: str | None = Field(sa_column=Column(Text, nullable=True))
    analysis: str | None = Field(sa_column=Column(Text, nullable=True))
    predict: str | None = Field(sa_column=Column(Text, nullable=True))
    predict_data: str | None = Field(sa_column=Column(Text, nullable=True))
    recommended_question_answer: str | None = Field(
        sa_column=Column(Text, nullable=True)
    )
    recommended_question: str | None = Field(sa_column=Column(Text, nullable=True))
    datasource_select_answer: str | None = Field(
        sa_column=Column(Text, nullable=True)
    )
    finish: bool = Field(
        sa_column=Column(Boolean, nullable=True, default=False)
    )
    status: str | None = Field(default=None, max_length=32, nullable=True)
    trace_id: str | None = Field(default=None, max_length=64, nullable=True)
    # 新记录默认进入 Graph；Agent 入口会显式覆盖为 agent。
    execution_type: str = Field(default="graph", max_length=32, nullable=False)
    error: str | None = Field(sa_column=Column(Text, nullable=True))
    analysis_record_id: int | None = Field(sa_column=Column(BigInteger, nullable=True))
    predict_record_id: int | None = Field(sa_column=Column(BigInteger, nullable=True))
    regenerate_record_id: int | None = Field(sa_column=Column(BigInteger, nullable=True))


__all__ = ["ChatFinishStep", "ChatRecord"]
