"""语义模型、模型关系、字段及度量 ORM 模型。"""

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


class SemanticModel(SQLModel, table=True):
    __tablename__ = "headless_model"
    __table_args__ = (
        Index("ux_headless_model_biz_name", "oid", "domain_id", "biz_name", unique=True),
        Index("idx_headless_model_status", "oid", "domain_id", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    domain_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    datasource_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    name: str = Field(max_length=128, nullable=False)
    biz_name: str = Field(max_length=128, nullable=False)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    status: int = Field(default=1, nullable=False)
    model_detail: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    depends: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    filter_sql: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    alias: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    source_type: str = Field(default="TABLE", max_length=32, nullable=False)
    database_name: str | None = Field(default=None, max_length=256)
    schema_name: str | None = Field(default=None, max_length=256)
    table_name: str | None = Field(default=None, max_length=256)
    sql_query: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    primary_key: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    model_grain: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    default_time_field: str | None = Field(default=None, max_length=128)
    is_view: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false")))
    schema_version: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default=text("1")))
    last_schema_sync_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )
    ext: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class SemanticModelRelation(SQLModel, table=True):
    __tablename__ = "headless_model_relation"
    __table_args__ = (
        Index("idx_headless_model_relation_domain", "oid", "domain_id", "status"),
        Index("idx_headless_model_relation_models", "oid", "left_model_id", "right_model_id", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    domain_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    left_model_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    right_model_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    join_type: str = Field(default="left join", max_length=32, nullable=False)
    join_conditions: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    status: int = Field(default=1, nullable=False)
    ext: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class SemanticModelField(SQLModel, table=True):
    __tablename__ = "headless_model_field"
    __table_args__ = (
        Index("ux_headless_model_field_biz_name", "oid", "model_id", "biz_name", unique=True),
        Index("idx_headless_model_field_role", "oid", "model_id", "field_role", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    model_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    field_name: str = Field(max_length=128, nullable=False)
    name: str = Field(max_length=128, nullable=False)
    biz_name: str = Field(max_length=128, nullable=False)
    expr: str = Field(sa_column=Column(Text, nullable=False))
    data_type: str | None = Field(default=None, max_length=64)
    field_role: str = Field(default="FIELD", max_length=32, nullable=False)
    semantic_type: str | None = Field(default=None, max_length=64)
    alias: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    type_params: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    source_order: int = Field(default=0, nullable=False)
    is_available: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))
    status: int = Field(default=1, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class SemanticModelMeasure(SQLModel, table=True):
    __tablename__ = "headless_model_measure"
    __table_args__ = (
        Index("ux_headless_model_measure_biz_name", "oid", "model_id", "biz_name", unique=True),
        Index("idx_headless_model_measure_status", "oid", "model_id", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    model_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    field_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    name: str = Field(max_length=128, nullable=False)
    biz_name: str = Field(max_length=128, nullable=False)
    expr: str = Field(sa_column=Column(Text, nullable=False))
    agg: str | None = Field(default=None, max_length=32)
    data_type: str | None = Field(default=None, max_length=64)
    alias: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    type_params: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    status: int = Field(default=1, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
