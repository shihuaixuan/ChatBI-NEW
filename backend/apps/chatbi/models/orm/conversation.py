from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Identity, Integer, Text
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def enum_values(enum_class: type[Enum]) -> list[str]:
    """返回数据库枚举使用的稳定值。"""

    return [str(status.value) for status in enum_class]


class TypeEnum(Enum):
    CHAT = "0"


class OperationEnum(Enum):
    GENERATE_SQL = "0"
    GENERATE_CHART = "1"
    ANALYSIS = "2"
    PREDICT_DATA = "3"
    GENERATE_RECOMMENDED_QUESTIONS = "4"
    GENERATE_SQL_WITH_PERMISSIONS = "5"
    CHOOSE_DATASOURCE = "6"
    GENERATE_DYNAMIC_SQL = "7"
    CHOOSE_TABLE = "8"
    FILTER_TERMS = "9"
    FILTER_SQL_EXAMPLE = "10"
    FILTER_CUSTOM_PROMPT = "11"
    EXECUTE_SQL = "12"
    GENERATE_PICTURE = "13"
    FILTER_SEMANTIC_ASSET = "14"


class ChatFinishStep(Enum):
    GENERATE_SQL = 1
    QUERY_DATA = 2
    GENERATE_CHART = 3


class QuickCommand(Enum):
    REGENERATE = "/regenerate"
    ANALYSIS = "/analysis"
    PREDICT_DATA = "/predict"


class ChatLog(SQLModel, table=True):
    __tablename__ = "chat_log"

    id: int | None = Field(
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True)
    )
    type: TypeEnum = Field(
        sa_column=Column(
            SQLAlchemyEnum(
                TypeEnum,
                native_enum=False,
                values_callable=enum_values,
                length=3,
            )
        )
    )
    operate: OperationEnum = Field(
        sa_column=Column(
            SQLAlchemyEnum(
                OperationEnum,
                native_enum=False,
                values_callable=enum_values,
                length=3,
            )
        )
    )
    pid: int | None = Field(sa_column=Column(BigInteger, nullable=True))
    ai_modal_id: int | None = Field(sa_column=Column(BigInteger))
    base_modal: str | None = Field(max_length=255)
    messages: list[dict[str, Any]] | None = Field(sa_column=Column(JSONB))
    reasoning_content: str | None = Field(
        sa_column=Column(Text, nullable=True)
    )
    start_time: datetime = Field(
        sa_column=Column(DateTime(timezone=False), nullable=True)
    )
    finish_time: datetime | None = Field(
        sa_column=Column(DateTime(timezone=False), nullable=True)
    )
    token_usage: dict[str, Any] | int | None = Field(sa_column=Column(JSONB))
    local_operation: bool = Field(default=False)
    error: bool = Field(default=False)


class Chat(SQLModel, table=True):
    __tablename__ = "chat"

    id: int | None = Field(
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True)
    )
    oid: int | None = Field(
        sa_column=Column(BigInteger, nullable=True, default=1)
    )
    create_time: datetime = Field(
        sa_column=Column(DateTime(timezone=False), nullable=True)
    )
    create_by: int = Field(sa_column=Column(BigInteger, nullable=True))
    brief: str = Field(max_length=64, nullable=True)
    chat_type: str = Field(max_length=20, default="chat")
    dataset_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    datasource: int = Field(sa_column=Column(BigInteger, nullable=True))
    engine_type: str = Field(max_length=64)
    origin: int | None = Field(
        sa_column=Column(Integer, nullable=False, default=0)
    )
    brief_generate: bool = Field(default=False)
    recommended_question_answer: str = Field(
        sa_column=Column(Text, nullable=True)
    )
    recommended_question: str = Field(sa_column=Column(Text, nullable=True))
    recommended_generate: bool = Field(default=False)


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


__all__ = [
    "Chat",
    "ChatFinishStep",
    "ChatLog",
    "ChatRecord",
    "OperationEnum",
    "QuickCommand",
    "TypeEnum",
]
