"""主题域及其术语 ORM 模型。"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Identity,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class SemanticDomain(SQLModel, table=True):
    __tablename__ = "headless_domain"
    __table_args__ = (
        Index("ux_headless_domain_biz_name", "oid", "biz_name", unique=True),
        Index("idx_headless_domain_status", "oid", "status"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    name: str = Field(max_length=128, nullable=False)
    biz_name: str = Field(max_length=128, nullable=False)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    parent_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    status: int = Field(default=1, nullable=False)
    admin: str | None = Field(default=None, max_length=256)
    owner: str | None = Field(default=None, max_length=128)
    is_open: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))
    created_by: str | None = Field(default=None, max_length=128)
    updated_by: str | None = Field(default=None, max_length=128)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


class SemanticTerm(SQLModel, table=True):
    __tablename__ = "headless_term"
    __table_args__ = (Index("idx_headless_term_domain", "oid", "domain_id", "status"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    domain_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    name: str = Field(max_length=128, nullable=False)
    alias: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    related_metrics: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    related_dimensions: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    related_datasets: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    status: int = Field(default=1, nullable=False)
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
