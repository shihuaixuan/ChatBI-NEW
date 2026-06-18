from datetime import datetime

from pydantic import field_validator
from sqlalchemy import BigInteger, Column, DateTime, Identity, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from apps.semantic.assets.enums import AssetStatus


class SemanticModel(SQLModel, table=True):
    __tablename__ = "semantic_model"
    __table_args__ = (
        Index("ux_semantic_model_name", "dataset_id", "name", unique=True),
        Index("idx_semantic_model_status", "dataset_id", "status"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    dataset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    name: str = Field(max_length=128, nullable=False)
    source_type: str = Field(default="TABLE", max_length=32, nullable=False)
    table_ids: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    base_sql: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    primary_key_dimension_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    default_time_dimension_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    filter_sql: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    status: str = Field(default=AssetStatus.CANDIDATE.value, max_length=32, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))

    def __init__(self, **data):
        super().__init__(**data)
        if self.source_type == "SQL" and not self.base_sql:
            raise ValueError("base_sql is required when source_type is SQL")

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value: str) -> str:
        allowed = {"TABLE", "MULTI_TABLE", "VIEW", "SQL"}
        if value not in allowed:
            raise ValueError("source_type must be one of TABLE, MULTI_TABLE, VIEW, SQL")
        return value

    @field_validator("base_sql", mode="after")
    @classmethod
    def validate_base_sql(cls, value: str | None, info) -> str | None:
        if info.data.get("source_type") == "SQL" and not value:
            raise ValueError("base_sql is required when source_type is SQL")
        return value
