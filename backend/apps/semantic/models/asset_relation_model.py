from datetime import datetime

from pydantic import field_validator
from sqlalchemy import BigInteger, Column, DateTime, Float, Identity, Index, Text
from sqlmodel import Field, SQLModel

from apps.semantic.assets.enums import AssetStatus


class AssetRelation(SQLModel, table=True):
    __tablename__ = "semantic_asset_relation"
    __table_args__ = (
        Index(
            "ux_semantic_asset_relation",
            "oid",
            "dataset_id",
            "source_asset_type",
            "source_asset_id",
            "target_asset_type",
            "target_asset_id",
            "relation_type",
            unique=True,
        ),
        Index("idx_semantic_asset_relation_source", "source_asset_type", "source_asset_id"),
        Index("idx_semantic_asset_relation_target", "target_asset_type", "target_asset_id"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(default=1, sa_column=Column(BigInteger, nullable=False))
    datasource_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    dataset_id: str | None = Field(default=None, max_length=128)
    source_asset_type: str = Field(max_length=32, nullable=False)
    source_asset_id: str = Field(max_length=128, nullable=False)
    target_asset_type: str = Field(max_length=32, nullable=False)
    target_asset_id: str = Field(max_length=128, nullable=False)
    relation_type: str = Field(max_length=32, nullable=False)
    weight: float = Field(default=1.0, sa_column=Column(Float, nullable=False))
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    status: str = Field(default=AssetStatus.APPROVED.value, max_length=32, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))

    def __init__(self, **data):
        super().__init__(**data)
        if self.weight < 0 or self.weight > 1:
            raise ValueError("weight must be between 0.0 and 1.0")

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("weight must be between 0.0 and 1.0")
        return value
