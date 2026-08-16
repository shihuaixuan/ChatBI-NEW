from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import VECTOR  # type: ignore[import-untyped]
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Identity,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class SQLExampleModel(SQLModel, table=True):
    """SQL 示例持久化对象，继续映射原 data_training 表。"""

    __tablename__ = "data_training"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True),
    )
    oid: int | None = Field(
        default=1,
        sa_column=Column(BigInteger, nullable=True, default=1),
    )
    datasource: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )
    question: str | None = Field(default=None, max_length=255)
    description: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    example_type: str | None = Field(default="QUESTION_EXAMPLE", max_length=32)
    sql: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    linked_assets: list[dict[str, Any]] | None = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=True),
    )
    dataset_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(VECTOR(), nullable=True),
    )
    enabled: bool | None = Field(
        default=True,
        sa_column=Column(Boolean, default=True),
    )
    verification_status: str = Field(
        default="UNVERIFIED",
        sa_column=Column(
            String(32),
            nullable=False,
            server_default=text("'UNVERIFIED'"),
        ),
    )
    advanced_application: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    # ---- verified query 资产化（P0-5，迁移 110）----
    source: str = Field(
        default="manual",
        sa_column=Column(
            String(32),
            nullable=False,
            server_default=text("'manual'"),
        ),
    )
    verified_by: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    verified_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )
    semantic_plan: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    plan_fingerprint: str | None = Field(
        default=None,
        max_length=128,
    )
    use_as_onboarding: bool = Field(
        default=False,
        sa_column=Column(
            Boolean,
            nullable=False,
            server_default=text("false"),
        ),
    )
