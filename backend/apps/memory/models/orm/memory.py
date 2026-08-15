"""用户记忆持久化模型。"""

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import VECTOR  # type: ignore[import-untyped]
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class ChatbiMemory(SQLModel, table=True):
    """用户长期记忆主表。"""

    __tablename__ = "chatbi_memory"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True),
    )
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    user_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    layer: str = Field(default="atom", sa_column=Column(String(16), nullable=False))
    memory_type: str = Field(sa_column=Column(String(64), nullable=False))
    memory_key: str = Field(sa_column=Column(String(160), nullable=False))
    statement: str = Field(sa_column=Column(Text, nullable=False))
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False),
    )
    confidence: float = Field(
        default=0.5,
        sa_column=Column(Numeric(5, 4), nullable=False),
    )
    evidence_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False),
    )
    session_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False),
    )
    version: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False),
    )
    status: str = Field(
        default="candidate",
        sa_column=Column(String(32), nullable=False),
    )
    last_confirmed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )
    last_used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )
    expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )


class ChatbiMemoryEvidence(SQLModel, table=True):
    """用户记忆证据表。"""

    __tablename__ = "chatbi_memory_evidence"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True),
    )
    memory_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    user_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    evidence_type: str = Field(sa_column=Column(String(64), nullable=False))
    source_ref: str | None = Field(
        default=None,
        sa_column=Column(String(160), nullable=True),
    )
    source_session_id: str | None = Field(
        default=None,
        sa_column=Column(String(160), nullable=True),
    )
    evidence_text: str = Field(sa_column=Column(Text, nullable=False))
    strength: float = Field(
        default=0.5,
        sa_column=Column(Numeric(5, 4), nullable=False),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )


class ChatbiMemoryEmbedding(SQLModel, table=True):
    """用户记忆独立向量索引，不进入记忆公开 DTO。"""

    __tablename__ = "chatbi_memory_embedding"
    __table_args__ = (
        UniqueConstraint(
            "memory_id",
            "embedding_profile",
            name="ux_chatbi_memory_embedding_profile",
        ),
        Index(
            "idx_chatbi_memory_embedding_lookup",
            "oid",
            "user_id",
            "embedding_profile",
            "status",
        ),
        Index(
            "idx_chatbi_memory_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("status = 'active' AND embedding IS NOT NULL"),
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True),
    )
    memory_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    user_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    embedding_profile: str = Field(sa_column=Column(String(64), nullable=False))
    provider: str = Field(sa_column=Column(String(64), nullable=False))
    model: str = Field(sa_column=Column(String(128), nullable=False))
    dimension: int = Field(
        default=1024,
        sa_column=Column(Integer, nullable=False, server_default="1024"),
    )
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(VECTOR(1024), nullable=True),
    )
    status: str = Field(
        default="active",
        sa_column=Column(String(32), nullable=False, server_default="active"),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )


class ChatbiMemoryUsage(SQLModel, table=True):
    """用户记忆进入 Agent 上下文的使用记录。"""

    __tablename__ = "chatbi_memory_usage"
    __table_args__ = (
        Index(
            "idx_chatbi_memory_usage_user_time",
            "oid",
            "user_id",
            "created_at",
        ),
        Index(
            "idx_chatbi_memory_usage_memory_time",
            "memory_id",
            "created_at",
        ),
        Index(
            "idx_chatbi_memory_usage_variant_time",
            "oid",
            "user_id",
            "recall_variant",
            "created_at",
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True),
    )
    memory_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    user_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    session_id: str | None = Field(
        default=None,
        sa_column=Column(String(160), nullable=True),
    )
    run_id: str | None = Field(
        default=None,
        sa_column=Column(String(160), nullable=True),
    )
    stage: str = Field(sa_column=Column(String(64), nullable=False))
    matched_by: str = Field(sa_column=Column(String(32), nullable=False))
    recall_variant: str = Field(
        default="disabled",
        sa_column=Column(String(16), nullable=False, server_default="disabled"),
    )
    adopted: bool | None = Field(
        default=None,
        sa_column=Column(Boolean, nullable=True),
    )
    adopted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )


__all__ = [
    "ChatbiMemory",
    "ChatbiMemoryEmbedding",
    "ChatbiMemoryEvidence",
    "ChatbiMemoryUsage",
]
