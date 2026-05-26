from datetime import datetime
from enum import Enum

from pgvector.sqlalchemy import VECTOR
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


class AssetStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    APPROVED = "APPROVED"
    DISABLED = "DISABLED"
    DEPRECATED = "DEPRECATED"


class AssetOrigin(str, Enum):
    FIELD_INIT = "FIELD_INIT"
    MANUAL = "MANUAL"
    SQL_EXAMPLE = "SQL_EXAMPLE"
    IMPORT = "IMPORT"


class MetricDefineType(str, Enum):
    MEASURE = "MEASURE"
    DERIVED = "DERIVED"


class AggregationType(str, Enum):
    NONE = "NONE"
    SUM = "SUM"
    AVG = "AVG"
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    MAX = "MAX"
    MIN = "MIN"


class DimensionType(str, Enum):
    CATEGORY = "CATEGORY"
    TIME = "TIME"
    GEO = "GEO"
    ID = "ID"


class SemanticType(str, Enum):
    UNKNOWN = "UNKNOWN"
    DATE = "DATE"
    DATETIME = "DATETIME"
    REGION = "REGION"
    CHANNEL = "CHANNEL"
    USER = "USER"
    PRODUCT = "PRODUCT"
    ORGANIZATION = "ORGANIZATION"


class AssetType(str, Enum):
    METRIC = "METRIC"
    DIMENSION = "DIMENSION"
    VALUE = "VALUE"


class UsageRole(str, Enum):
    MATCHED = "MATCHED"
    INJECTED = "INJECTED"
    USED = "USED"


class SemanticMetric(SQLModel, table=True):
    __tablename__ = "semantic_metric"
    __table_args__ = (
        Index("ux_semantic_metric_name", "oid", "datasource_id", "name", unique=True),
        Index("idx_semantic_metric_ds_status", "oid", "datasource_id", "status"),
        Index("idx_semantic_metric_source", "table_id", "field_id"),
        Index("idx_semantic_metric_updated", text("updated_at DESC")),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    datasource_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    table_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    field_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    name: str = Field(max_length=128, nullable=False)
    display_name: str = Field(max_length=128, nullable=False)
    aliases: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    define_type: str = Field(default=MetricDefineType.MEASURE.value, max_length=32, nullable=False)
    expr: str = Field(sa_column=Column(Text, nullable=False))
    default_agg: str = Field(default=AggregationType.SUM.value, max_length=32, nullable=False)
    filter_sql: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    data_type: str | None = Field(default=None, max_length=64)
    data_format: str | None = Field(default=None, max_length=64)
    default_time_dimension_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    related_dimension_ids: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    owner_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    status: str = Field(default=AssetStatus.CANDIDATE.value, max_length=32, nullable=False)
    origin: str = Field(default=AssetOrigin.FIELD_INIT.value, max_length=32, nullable=False)
    embedding: list[float] | None = Field(default=None, sa_column=Column(VECTOR(), nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    created_by: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    updated_by: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))


class SemanticDimension(SQLModel, table=True):
    __tablename__ = "semantic_dimension"
    __table_args__ = (
        Index("ux_semantic_dimension_name", "oid", "datasource_id", "name", unique=True),
        Index("idx_semantic_dimension_ds_status", "oid", "datasource_id", "status"),
        Index("idx_semantic_dimension_source", "table_id", "field_id"),
        Index("idx_semantic_dimension_type", "dimension_type", "semantic_type"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    datasource_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    table_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    field_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    name: str = Field(max_length=128, nullable=False)
    display_name: str = Field(max_length=128, nullable=False)
    aliases: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    expr: str = Field(sa_column=Column(Text, nullable=False))
    dimension_type: str = Field(default=DimensionType.CATEGORY.value, max_length=32, nullable=False)
    semantic_type: str = Field(default=SemanticType.UNKNOWN.value, max_length=32, nullable=False)
    data_type: str | None = Field(default=None, max_length=64)
    time_granularities: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    default_values: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    owner_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    status: str = Field(default=AssetStatus.CANDIDATE.value, max_length=32, nullable=False)
    origin: str = Field(default=AssetOrigin.FIELD_INIT.value, max_length=32, nullable=False)
    embedding: list[float] | None = Field(default=None, sa_column=Column(VECTOR(), nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    created_by: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    updated_by: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))


class SemanticDimensionValue(SQLModel, table=True):
    __tablename__ = "semantic_dimension_value"
    __table_args__ = (
        Index("ux_semantic_dimension_value", "dimension_id", "value", unique=True),
        Index("idx_semantic_dimension_value_enabled", "dimension_id", "enabled"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    dimension_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    value: str = Field(sa_column=Column(Text, nullable=False))
    display_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    aliases: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))
    embedding: list[float] | None = Field(default=None, sa_column=Column(VECTOR(), nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class SemanticAssetAudit(SQLModel, table=True):
    __tablename__ = "semantic_asset_audit"
    __table_args__ = (Index("idx_semantic_asset_audit_asset", "asset_type", "asset_id", text("created_at DESC")),)

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    asset_type: str = Field(max_length=32, nullable=False)
    asset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    action: str = Field(max_length=32, nullable=False)
    before: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    after: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    created_by: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))


class ChatRecordSemanticAsset(SQLModel, table=True):
    __tablename__ = "chat_record_semantic_asset"
    __table_args__ = (
        Index("ux_chat_record_semantic_asset", "record_id", "asset_type", "asset_id", "role", unique=True),
        Index("idx_chat_record_semantic_asset_record", "record_id"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    record_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    asset_type: str = Field(max_length=32, nullable=False)
    asset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    role: str = Field(max_length=32, nullable=False)
    match_word: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    score: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    snapshot: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
