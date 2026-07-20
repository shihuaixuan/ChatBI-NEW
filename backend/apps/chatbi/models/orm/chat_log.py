"""会话 ORM：chat_log 表与步骤日志枚举。"""

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Identity, Text
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


__all__ = ["ChatLog", "OperationEnum", "TypeEnum", "enum_values"]
