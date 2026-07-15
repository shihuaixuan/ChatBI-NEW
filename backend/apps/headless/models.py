from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Identity,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class HeadlessDomain(SQLModel, table=True):
    __tablename__ = "headless_domain"
    __table_args__ = (
        Index("ux_headless_domain_biz_name", "oid", "biz_name", unique=True),
        Index("idx_headless_domain_status", "oid", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    name: str = Field(max_length=128, nullable=False)
    biz_name: str = Field(max_length=128, nullable=False)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    parent_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    status: int = Field(default=1, nullable=False)
    admin: str | None = Field(default=None, max_length=256)
    owner: str | None = Field(default=None, max_length=128)
    is_open: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))
    created_by: str | None = Field(default=None, max_length=128)
    updated_by: str | None = Field(default=None, max_length=128)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class HeadlessModel(SQLModel, table=True):
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


class HeadlessModelRelation(SQLModel, table=True):
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


class HeadlessMetric(SQLModel, table=True):
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
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class HeadlessDimension(SQLModel, table=True):
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
    is_tag: int = Field(default=0, nullable=False)
    ext: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class HeadlessDataSet(SQLModel, table=True):
    __tablename__ = "headless_dataset"
    __table_args__ = (
        Index("ux_headless_dataset_biz_name", "oid", "domain_id", "biz_name", unique=True),
        Index("idx_headless_dataset_status", "oid", "domain_id", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    domain_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    name: str = Field(max_length=128, nullable=False)
    biz_name: str = Field(max_length=128, nullable=False)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    status: int = Field(default=1, nullable=False)
    alias: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    data_set_detail: dict[str, Any] = Field(
        default_factory=lambda: {"dataSetModelConfigs": []},
        sa_column=Column(JSONB, nullable=False, server_default=text("'{\"dataSetModelConfigs\": []}'::jsonb")),
    )
    query_config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    schema_version: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default=text("1")))
    index_version: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0")))
    default_model_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    default_time_dimension_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    owner: str | None = Field(default=None, max_length=128)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class HeadlessTerm(SQLModel, table=True):
    __tablename__ = "headless_term"
    __table_args__ = (Index("idx_headless_term_domain", "oid", "domain_id", "status"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    domain_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    name: str = Field(max_length=128, nullable=False)
    alias: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    related_metrics: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    related_dimensions: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    status: int = Field(default=1, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class HeadlessModelField(SQLModel, table=True):
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


class HeadlessModelMeasure(SQLModel, table=True):
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


class HeadlessDimensionValue(SQLModel, table=True):
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


class HeadlessDataSetModelConfig(SQLModel, table=True):
    __tablename__ = "headless_dataset_model_config"
    __table_args__ = (Index("ux_headless_dataset_model", "oid", "dataset_id", "model_id", unique=True),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    dataset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    model_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    includes_all: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false")))
    is_default: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false")))
    sort_order: int = Field(default=0, nullable=False)
    status: int = Field(default=1, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class HeadlessDataSetAsset(SQLModel, table=True):
    __tablename__ = "headless_dataset_asset"
    __table_args__ = (
        Index("ux_headless_dataset_asset", "oid", "dataset_id", "asset_type", "asset_id", unique=True),
        Index("idx_headless_dataset_asset_model", "oid", "dataset_id", "model_id", "asset_type", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    dataset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    model_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    asset_type: str = Field(max_length=32, nullable=False)
    asset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    is_default: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false")))
    sort_order: int = Field(default=0, nullable=False)
    status: int = Field(default=1, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class HeadlessAssetAlias(SQLModel, table=True):
    __tablename__ = "headless_asset_alias"
    __table_args__ = (
        Index("idx_headless_asset_alias_lookup", "oid", "asset_type", "asset_id", "status"),
        Index("idx_headless_asset_alias_text", "oid", "alias", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    asset_type: str = Field(max_length=32, nullable=False)
    asset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    alias: str = Field(sa_column=Column(Text, nullable=False))
    alias_type: str = Field(default="MANUAL", max_length=32, nullable=False)
    language: str | None = Field(default=None, max_length=32)
    priority: int = Field(default=0, nullable=False)
    status: int = Field(default=1, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class HeadlessAssetRelation(SQLModel, table=True):
    __tablename__ = "headless_asset_relation"
    __table_args__ = (
        Index("idx_headless_asset_relation_source", "oid", "source_type", "source_id", "relation_type", "status"),
        Index("idx_headless_asset_relation_target", "oid", "target_type", "target_id", "relation_type", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    source_type: str = Field(max_length=32, nullable=False)
    source_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    relation_type: str = Field(max_length=64, nullable=False)
    target_type: str = Field(max_length=32, nullable=False)
    target_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    weight: float = Field(default=1.0, sa_column=Column(Float, nullable=False, server_default=text("1.0")))
    relation_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    status: int = Field(default=1, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class HeadlessAssetDocument(SQLModel, table=True):
    __tablename__ = "headless_asset_document"
    __table_args__ = (
        Index("ux_headless_asset_document", "oid", "dataset_id", "asset_type", "asset_id", unique=True),
        Index("idx_headless_asset_document_dataset", "oid", "dataset_id", "index_version"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    dataset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    asset_type: str = Field(max_length=32, nullable=False)
    asset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    doc_key: str = Field(max_length=128, nullable=False)
    title: str = Field(sa_column=Column(Text, nullable=False))
    business_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    technical_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    alias_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    search_text: str = Field(sa_column=Column(Text, nullable=False))
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    index_version: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0")))
    embedding_status: str = Field(default="PENDING", max_length=32, nullable=False)
    embedding_ref: str | None = Field(default=None, max_length=256)
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class HeadlessSchemaIndex(SQLModel, table=True):
    __tablename__ = "headless_schema_index"
    __table_args__ = (
        Index("ux_headless_schema_index_element", "oid", "dataset_id", "element_type", "element_id", unique=True),
        Index("idx_headless_schema_index_text", "oid", "dataset_id", "element_type"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    dataset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    element_type: str = Field(max_length=32, nullable=False)
    element_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    search_text: str = Field(sa_column=Column(Text, nullable=False))
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
