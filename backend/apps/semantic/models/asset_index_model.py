from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Identity, Index
from sqlmodel import Field, SQLModel


class AssetIndexVersion(SQLModel, table=True):
    __tablename__ = "semantic_asset_index_version"
    __table_args__ = (
        Index("ux_semantic_asset_index_version", "oid", "dataset_id", "index_type", unique=True),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    dataset_id: str = Field(max_length=128, nullable=False)
    index_type: str = Field(max_length=64, nullable=False)
    version: str = Field(max_length=128, nullable=False)
    status: str = Field(default="READY", max_length=32, nullable=False)
    last_built_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
