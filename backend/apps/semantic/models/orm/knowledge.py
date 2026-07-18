"""语义资产别名与关系 ORM 模型。"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Float, Identity, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class SemanticAssetAlias(SQLModel, table=True):
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


class SemanticAssetRelation(SQLModel, table=True):
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
