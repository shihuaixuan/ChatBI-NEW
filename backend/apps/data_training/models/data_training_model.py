from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from pydantic import BaseModel
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Identity, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class DataTraining(SQLModel, table=True):
    __tablename__ = "data_training"
    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int | None = Field(sa_column=Column(BigInteger, nullable=True, default=1))
    datasource: int | None = Field(sa_column=Column(BigInteger, nullable=True))
    create_time: datetime | None = Field(sa_column=Column(DateTime(timezone=False), nullable=True))
    question: str | None = Field(max_length=255)
    description: str | None = Field(sa_column=Column(Text, nullable=True))
    example_type: str | None = Field(max_length=32, default="QUESTION_EXAMPLE")
    sql: str | None = Field(sa_column=Column(Text, nullable=True), default=None)
    linked_assets: list[dict] | None = Field(sa_column=Column(JSONB, nullable=True), default=[])
    dataset_id: int | None = Field(sa_column=Column(BigInteger, nullable=True), default=None)
    embedding: list[float] | None = Field(sa_column=Column(VECTOR(), nullable=True))
    enabled: bool | None = Field(sa_column=Column(Boolean, default=True))
    advanced_application: int | None = Field(sa_column=Column(BigInteger, nullable=True))


class DataTrainingInfo(BaseModel):
    id: int | None = None
    oid: int | None = None
    datasource: int | None = None
    datasource_name: str | None = None
    create_time: datetime | None = None
    question: str | None = None
    description: str | None = None
    example_type: str | None = "QUESTION_EXAMPLE"
    sql: str | None = None
    linked_assets: list[dict] | None = []
    dataset_id: int | None = None
    enabled: bool | None = True
    advanced_application: int | None = None
    advanced_application_name: str | None = None


class DataTrainingInfoResult(BaseModel):
    id: str | None = None
    oid: str | None = None
    datasource: int | None = None
    datasource_name: str | None = None
    create_time: datetime | None = None
    question: str | None = None
    description: str | None = None
    example_type: str | None = "QUESTION_EXAMPLE"
    sql: str | None = None
    linked_assets: list[dict] | None = []
    dataset_id: int | None = None
    enabled: bool | None = True
    advanced_application: str | None = None
    advanced_application_name: str | None = None
