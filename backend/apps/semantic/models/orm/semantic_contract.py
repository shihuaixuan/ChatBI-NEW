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
    status: int = Field(default=1, nullable=False)
    version: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default=text("1")))
    ext: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


__all__ = ["BusinessEntity", "LogicalDimension", "MetricDimensionCapability"]
