from datetime import datetime

from pydantic import field_validator
from sqlalchemy import BigInteger, Column, DateTime, Float, Identity, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class AssetQualityScore(SQLModel, table=True):
    __tablename__ = "semantic_asset_quality_score"
    __table_args__ = (
        Index("ux_semantic_asset_quality", "oid", "asset_type", "asset_id", "dataset_id", unique=True),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    asset_type: str = Field(max_length=32, nullable=False)
    asset_id: str = Field(max_length=128, nullable=False)
    dataset_id: str | None = Field(default=None, max_length=128)
    score: float = Field(default=1.0, sa_column=Column(Float, nullable=False))
    issues: list[dict] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    checked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))

    def __init__(self, **data):
        super().__init__(**data)
        if self.score < 0 or self.score > 1:
            raise ValueError("score must be between 0.0 and 1.0")

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("score must be between 0.0 and 1.0")
        return value
