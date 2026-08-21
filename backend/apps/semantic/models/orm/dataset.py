"""数据集及其模型、资产配置 ORM 模型。"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Identity,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class SemanticDataset(SQLModel, table=True):
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
    contract_version: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0")))
    default_timezone: str = Field(
        default="UTC",
        sa_column=Column(
            String(length=64),
            nullable=False,
            server_default=text("'UTC'"),
        ),
    )
    calendar_type: str = Field(
        default="NATURAL",
        sa_column=Column(
            String(length=32),
            nullable=False,
            server_default=text("'NATURAL'"),
        ),
    )
    week_start_day: int = Field(
        default=1,
        sa_column=Column(BigInteger, nullable=False, server_default=text("1")),
    )
    fiscal_year_start_month: int = Field(
        default=1,
        sa_column=Column(BigInteger, nullable=False, server_default=text("1")),
    )
    holiday_calendar_key: str | None = Field(
        default=None,
        sa_column=Column(String(length=128), nullable=True),
    )
    index_version: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0")))
    default_model_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    default_time_dimension_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    owner: str | None = Field(default=None, max_length=128)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class SemanticDatasetModelConfig(SQLModel, table=True):
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


class SemanticDatasetAsset(SQLModel, table=True):
    __tablename__ = "headless_dataset_asset"
    __table_args__ = (
        Index("ux_headless_dataset_asset", "oid", "dataset_id", "asset_type", "asset_id", unique=True),
        Index("idx_headless_dataset_asset_model", "oid", "dataset_id", "model_id", "asset_type", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    dataset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    # 层级和指标关系属于数据集级资产，不绑定具体模型。
    model_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    asset_type: str = Field(max_length=32, nullable=False)
    asset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    is_default: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false")))
    sort_order: int = Field(default=0, nullable=False)
    status: int = Field(default=1, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
