from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Identity, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from apps.semantic.assets.enums import AssetStatus


class SemanticDataset(SQLModel, table=True):
    __tablename__ = "semantic_dataset"
    __table_args__ = (
        Index("ux_semantic_dataset_name", "oid", "datasource_id", "name", unique=True),
        Index("idx_semantic_dataset_status", "oid", "datasource_id", "status"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    datasource_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    name: str = Field(max_length=128, nullable=False)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    business_domain: str | None = Field(default=None, max_length=128)
    default_time_dimension_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    default_metric_ids: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    default_filter: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    status: str = Field(default=AssetStatus.CANDIDATE.value, max_length=32, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))

