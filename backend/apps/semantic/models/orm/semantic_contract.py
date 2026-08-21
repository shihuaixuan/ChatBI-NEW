"""完整语义契约的持久化模型。"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Identity, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class BusinessEntity(SQLModel, table=True):
    """跨模型保持稳定身份的业务实体。"""

    __tablename__ = "headless_business_entity"
    __table_args__ = (
        Index("ux_headless_business_entity_biz_name", "oid", "domain_id", "biz_name", unique=True),
        Index("idx_headless_business_entity_status", "oid", "domain_id", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    domain_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    name: str = Field(max_length=128, nullable=False)
    biz_name: str = Field(max_length=128, nullable=False)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    key_type: str = Field(max_length=64, nullable=False)
    value_domain_key: str | None = Field(default=None, max_length=128)
    status: int = Field(default=1, nullable=False)
    version: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default=text("1")))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class LogicalDimension(SQLModel, table=True):
    """独立于物理模型的业务维度。"""

    __tablename__ = "headless_logical_dimension"
    __table_args__ = (
        Index("ux_headless_logical_dimension_biz_name", "oid", "domain_id", "biz_name", unique=True),
        Index("idx_headless_logical_dimension_status", "oid", "domain_id", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    domain_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    entity_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    name: str = Field(max_length=128, nullable=False)
    biz_name: str = Field(max_length=128, nullable=False)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    semantic_type: str = Field(max_length=64, nullable=False)
    value_type: str = Field(max_length=64, nullable=False)
    value_domain_key: str | None = Field(default=None, max_length=128)
    status: int = Field(default=1, nullable=False)
    version: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default=text("1")))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class MetricDimensionCapability(SQLModel, table=True):
    """指标使用业务维度时的可执行能力契约。"""

    __tablename__ = "headless_metric_dimension_capability"
    __table_args__ = (
        Index(
            "ux_headless_metric_dimension_capability",
            "oid",
            "metric_id",
            "logical_dimension_id",
            unique=True,
        ),
        Index("idx_headless_metric_dimension_capability_status", "oid", "metric_id", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    metric_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    logical_dimension_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    usages: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    binding_strategy: str = Field(max_length=32, nullable=False)
    relation_path: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    target_model_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    physical_dimension_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    aggregation_safety: str = Field(max_length=32, nullable=False)
    pre_aggregation_grain: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    time_alignment_policy: str = Field(max_length=32, nullable=False)
    # 贡献度结果必须使用服务端固定容差完成对账。
    contribution_tolerance: float = Field(default=1e-6, nullable=False)
    status: int = Field(default=1, nullable=False)
    version: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default=text("1")))
    ext: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class DimensionHierarchy(SQLModel, table=True):
    """逻辑维度之间的固定级别层级资产。"""

    __tablename__ = "headless_dimension_hierarchy"
    __table_args__ = (
        Index("ux_headless_dimension_hierarchy_biz_name", "oid", "domain_id", "biz_name", unique=True),
        Index("idx_headless_dimension_hierarchy_status", "oid", "domain_id", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    domain_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    name: str = Field(max_length=128, nullable=False)
    biz_name: str = Field(max_length=128, nullable=False)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    hierarchy_type: str = Field(default="FIXED_LEVEL", max_length=32, nullable=False)
    contract_status: str = Field(default="DRAFT", max_length=32, nullable=False)
    version: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default=text("1")))
    status: int = Field(default=1, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class DimensionHierarchyLevel(SQLModel, table=True):
    """维度层级中的有序逻辑维度节点。"""

    __tablename__ = "headless_dimension_hierarchy_level"
    __table_args__ = (
        Index("ux_headless_dimension_hierarchy_level_order", "hierarchy_id", "level_order", unique=True),
        Index("ux_headless_dimension_hierarchy_level_dimension", "hierarchy_id", "logical_dimension_id", unique=True),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    hierarchy_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    logical_dimension_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    level_order: int = Field(sa_column=Column(BigInteger, nullable=False))


class MetricRelationship(SQLModel, table=True):
    """目标指标与驱动指标之间的受治理分析关系。"""

    __tablename__ = "headless_metric_relationship"
    __table_args__ = (
        Index("ux_headless_metric_relationship_pair", "oid", "target_metric_id", "driver_metric_id", unique=True),
        Index("idx_headless_metric_relationship_status", "oid", "domain_id", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    domain_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    target_metric_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    driver_metric_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    relationship_type: str = Field(max_length=32, nullable=False)
    validation_method: str = Field(max_length=32, nullable=False)
    expected_direction: str = Field(default="UNKNOWN", max_length=16, nullable=False)
    supported_time_roles: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    relation_path: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    contract_status: str = Field(default="DRAFT", max_length=32, nullable=False)
    version: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default=text("1")))
    status: int = Field(default=1, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class MetricRelationshipDimension(SQLModel, table=True):
    """指标关系允许共同分析的逻辑维度。"""

    __tablename__ = "headless_metric_relationship_dimension"
    __table_args__ = (
        Index("ux_headless_metric_relationship_dimension", "relationship_id", "logical_dimension_id", unique=True),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    relationship_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    logical_dimension_id: int = Field(sa_column=Column(BigInteger, nullable=False))


__all__ = [
    "BusinessEntity",
    "LogicalDimension",
    "MetricDimensionCapability",
    "DimensionHierarchy",
    "DimensionHierarchyLevel",
    "MetricRelationship",
    "MetricRelationshipDimension",
]
