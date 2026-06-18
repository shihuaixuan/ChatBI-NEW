from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from pydantic import BaseModel
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Identity, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Terminology(SQLModel, table=True):
    __tablename__ = "terminology"
    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int | None = Field(sa_column=Column(BigInteger, nullable=True, default=1))
    pid: int | None = Field(sa_column=Column(BigInteger, nullable=True))
    create_time: datetime | None = Field(sa_column=Column(DateTime(timezone=False), nullable=True))
    word: str | None = Field(max_length=255)
    description: str | None = Field(sa_column=Column(Text, nullable=True))
    embedding: list[float] | None = Field(sa_column=Column(VECTOR(), nullable=True))
    aliases: list[str] | None = Field(sa_column=Column(JSONB, nullable=True), default=[])
    dataset_ids: list[int] | None = Field(sa_column=Column(JSONB, nullable=True), default=[])
    mapped_assets: list[dict] | None = Field(sa_column=Column(JSONB, nullable=True), default=[])
    specific_ds: bool | None = Field(sa_column=Column(Boolean, default=False))
    datasource_ids: list[int] | None = Field(sa_column=Column(JSONB), default=[])
    enabled: bool | None = Field(sa_column=Column(Boolean, default=True))


class TerminologyInfo(BaseModel):
    id: int | None = None
    create_time: datetime | None = None
    word: str | None = None
    description: str | None = None
    other_words: list[str] | None = []
    aliases: list[str] | None = []
    specific_ds: bool | None = False
    datasource_ids: list[int] | None = []
    dataset_ids: list[int] | None = []
    mapped_assets: list[dict] | None = []
    datasource_names: list[str] | None = []
    enabled: bool | None = True
