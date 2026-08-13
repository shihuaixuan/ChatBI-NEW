"""维度及维度值 ORM 模型。"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Identity,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class SemanticDimension(SQLModel, table=True):
    __tablename__ = "headless_dimension"
    __table_args__ = (
        Index("ux_headless_dimension_biz_name", "oid", "model_id", "biz_name", unique=True),
        Index("idx_headless_dimension_status", "oid", "model_id", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    model_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    name: str = Field(max_length=128, nullable=False)
    biz_name: str = Field(max_length=128, nullable=False)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    status: int = Field(default=1, nullable=False)
    sensitive_level: int = Field(default=0, nullable=False)
    type: str = Field(default="categorical", max_length=64, nullable=False)
    semantic_type: str | None = Field(default=None, max_length=64)
    alias: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    default_values: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    dim_value_maps: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    type_params: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    field_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    field_name: str | None = Field(default=None, max_length=128)
    is_primary_key: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false")))
    is_default_time: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false")))
    time_granularities: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    value_type: str | None = Field(default=None, max_length=64)
    value_source_type: str | None = Field(default=None, max_length=32)
    value_query_sql: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    expr: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    data_type: str | None = Field(default=None, max_length=64)
    logical_dimension_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    binding_role: str | None = Field(default=None, max_length=32)
    binding_priority: int | None = Field(default=None)
    contract_version: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    is_tag: int = Field(default=0, nullable=False)
    ext: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class SemanticDimensionValue(SQLModel, table=True):
    __tablename__ = "headless_dimension_value"
    __table_args__ = (
        Index("idx_headless_dimension_value_dimension", "oid", "dimension_id", "status"),
        Index("idx_headless_dimension_value_value", "oid", "dimension_id", "value"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    dimension_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    model_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    value: str = Field(sa_column=Column(Text, nullable=False))
    display_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    biz_name: str | None = Field(default=None, max_length=128)
    alias: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    source_type: str = Field(default="MANUAL", max_length=32, nullable=False)
    frequency: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0")))
    enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))
    status: int = Field(default=1, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
