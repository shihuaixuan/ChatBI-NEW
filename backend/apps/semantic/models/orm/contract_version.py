"""语义数据集契约发布版本的持久化模型。"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Identity, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class SemanticContractVersion(SQLModel, table=True):
    """保存一次已发布契约的不可变版本快照。"""

    __tablename__ = "headless_semantic_contract_version"
    __table_args__ = (
        Index(
            "ux_headless_semantic_contract_version",
            "oid",
            "dataset_id",
            "contract_version",
            unique=True,
        ),
        Index(
            "idx_headless_semantic_contract_version_dataset",
            "oid",
            "dataset_id",
            "published_at",
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True),
    )
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    dataset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    schema_version: int = Field(
        ge=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    contract_version: int = Field(
        ge=1,
        sa_column=Column(BigInteger, nullable=False),
    )
    schema_fingerprint: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    asset_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    published_by: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    published_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )


__all__ = ["SemanticContractVersion"]
