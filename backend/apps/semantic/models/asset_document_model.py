from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Identity, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class AssetRetrievalDocument(SQLModel, table=True):
    __tablename__ = "semantic_asset_retrieval_document"
    __table_args__ = (
        Index("ux_semantic_asset_retrieval_document", "oid", "dataset_id", "asset_type", "asset_id", unique=True),
        Index("idx_semantic_asset_retrieval_document_doc_id", "doc_id", unique=False),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    dataset_id: str = Field(max_length=128, nullable=False)
    asset_type: str = Field(max_length=32, nullable=False)
    asset_id: str = Field(max_length=128, nullable=False)
    doc_id: str = Field(max_length=192, nullable=False)
    title: str = Field(max_length=256, nullable=False)
    aliases: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    business_text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    technical_text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    related_terms: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    related_examples: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    relations: list[dict] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    search_text: str = Field(sa_column=Column(Text, nullable=False))
    version: str = Field(default="dynamic", max_length=128, nullable=False)
    metadata_json: dict = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
