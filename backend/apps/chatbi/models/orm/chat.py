"""会话 ORM：chat 表。"""

from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, Column, DateTime, Identity, Integer, Text
from sqlmodel import Field, SQLModel


class QuickCommand(Enum):
    REGENERATE = "/regenerate"
    ANALYSIS = "/analysis"
    PREDICT_DATA = "/predict"


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


__all__ = ["Chat", "QuickCommand"]
