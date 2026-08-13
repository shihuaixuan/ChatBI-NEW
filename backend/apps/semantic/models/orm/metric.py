"""指标 ORM 模型。"""

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


class SemanticMetric(SQLModel, table=True):
    __tablename__ = "headless_metric"
    __table_args__ = (
        Index("ux_headless_metric_biz_name", "oid", "model_id", "biz_name", unique=True),
        Index("idx_headless_metric_status", "oid", "model_id", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    model_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    name: str = Field(max_length=128, nullable=False)
    biz_name: str = Field(max_length=128, nullable=False)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    status: int = Field(default=1, nullable=False)
    sensitive_level: int = Field(default=0, nullable=False)
    type: str = Field(default="ATOMIC", max_length=32, nullable=False)
    default_agg: str | None = Field(default=None, max_length=32)
    data_format_type: str | None = Field(default=None, max_length=64)
    data_format: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    alias: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    classifications: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    relate_dimensions: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    type_params: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    ext: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    define_type: str = Field(default="MEASURE", max_length=32, nullable=False)
    measure_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    field_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    expr: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    filter_sql: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    fields: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    metric_refs: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    version: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default=text("1")))
    quality_status: str | None = Field(default=None, max_length=32)
    quality_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    is_publish: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))
    is_tag: int = Field(default=0, nullable=False)
    result_grain: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    additivity: str | None = Field(default=None, max_length=32)
    distinct_keys: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    time_semantics: str | None = Field(default=None, max_length=32)
    default_time_dimension_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    snapshot_aggregation: str | None = Field(default=None, max_length=32)
    contract_version: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
