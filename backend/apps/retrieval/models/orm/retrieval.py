"""统一检索平台的 PostgreSQL 持久化模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import VECTOR  # type: ignore[import-untyped]
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlmodel import Field, SQLModel


class RetrievalSourceModel(SQLModel, table=True):
    """一个可独立同步、授权和切换 generation 的检索来源。"""

    __tablename__ = "retrieval_source"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="ux_retrieval_source_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "source_type",
            "source_key",
            name="ux_retrieval_source_key",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive', 'rebuilding', 'failed', 'deleted')",
            name="ck_retrieval_source_status",
        ),
        Index("idx_retrieval_source_lookup", "tenant_id", "source_type", "status"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    tenant_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    source_type: str = Field(sa_column=Column(String(32), nullable=False))
    source_key: str = Field(sa_column=Column(String(256), nullable=False))
    namespace: str = Field(sa_column=Column(String(128), nullable=False))
    source_config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("config", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    acl_policy: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    source_version: str = Field(sa_column=Column(String(128), nullable=False))
    active_generation: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    status: str = Field(
        default="active",
        sa_column=Column(String(32), nullable=False, server_default=text("'active'")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class RetrievalIndexGenerationModel(SQLModel, table=True):
    """一次可原子激活、失败隔离和回滚的完整来源索引快照。"""

    __tablename__ = "retrieval_index_generation"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "generation",
            name="ux_retrieval_index_generation_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["retrieval_source.tenant_id", "retrieval_source.id"],
            name="fk_retrieval_index_generation_source_tenant",
        ),
        CheckConstraint(
            "status IN ('building', 'ready', 'active', 'superseded', 'failed', 'cancelled')",
            name="ck_retrieval_index_generation_status",
        ),
        CheckConstraint(
            "expected_jobs >= 0 AND succeeded_jobs >= 0 AND failed_jobs >= 0",
            name="ck_retrieval_index_generation_job_counts",
        ),
        CheckConstraint(
            "resource_count >= 0 AND unit_count >= 0 AND embedding_count >= 0",
            name="ck_retrieval_index_generation_asset_counts",
        ),
        CheckConstraint(
            "embedding_dimension = 1024",
            name="ck_retrieval_index_generation_dimension",
        ),
        Index(
            "ux_retrieval_index_generation_active",
            "source_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "idx_retrieval_index_generation_source",
            "tenant_id",
            "source_id",
            "status",
            "created_at",
        ),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    tenant_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    source_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    generation: str = Field(sa_column=Column(String(64), nullable=False))
    source_version: str = Field(sa_column=Column(String(128), nullable=False))
    previous_generation: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    embedding_profile: str = Field(sa_column=Column(String(64), nullable=False))
    embedding_provider: str = Field(sa_column=Column(String(64), nullable=False))
    embedding_model: str = Field(sa_column=Column(String(128), nullable=False))
    embedding_dimension: int = Field(
        default=1024,
        sa_column=Column(Integer, nullable=False, server_default=text("1024")),
    )
    status: str = Field(
        default="building",
        sa_column=Column(String(32), nullable=False, server_default=text("'building'")),
    )
    expected_jobs: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    succeeded_jobs: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    failed_jobs: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    resource_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    unit_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    embedding_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    error_code: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    activated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class RetrievalResourceModel(SQLModel, table=True):
    """可返回给调用方的稳定业务资源。"""

    __tablename__ = "retrieval_resource"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="ux_retrieval_resource_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "id",
            name="ux_retrieval_resource_source_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "resource_type",
            "source_resource_id",
            name="ux_retrieval_resource_source_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["retrieval_source.tenant_id", "retrieval_source.id"],
            name="fk_retrieval_resource_source_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_resource_id"],
            ["retrieval_resource.tenant_id", "retrieval_resource.id"],
            name="fk_retrieval_resource_parent_tenant",
        ),
        CheckConstraint(
            "NOT (dataset_id IS NOT NULL AND knowledge_base_id IS NOT NULL)",
            name="ck_retrieval_resource_scope_exclusive",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive', 'deleted')",
            name="ck_retrieval_resource_status",
        ),
        CheckConstraint(
            "visibility IN ('private', 'tenant', 'public')",
            name="ck_retrieval_resource_visibility",
        ),
        Index(
            "idx_retrieval_resource_dataset",
            "tenant_id",
            "namespace",
            "dataset_id",
            "resource_type",
            "status",
        ),
        Index(
            "idx_retrieval_resource_knowledge",
            "tenant_id",
            "namespace",
            "knowledge_base_id",
            "resource_type",
            "status",
        ),
        Index("idx_retrieval_resource_source", "tenant_id", "source_id", "status"),
        Index(
            "idx_retrieval_resource_title_trgm",
            text("title gin_trgm_ops"),
            postgresql_using="gin",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    tenant_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    namespace: str = Field(sa_column=Column(String(128), nullable=False))
    resource_type: str = Field(sa_column=Column(String(32), nullable=False))
    source_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    source_resource_id: str = Field(sa_column=Column(String(256), nullable=False))
    parent_resource_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    dataset_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    knowledge_base_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    title: str = Field(sa_column=Column(Text, nullable=False))
    resource_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    acl: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    visibility: str = Field(
        default="tenant",
        sa_column=Column(String(32), nullable=False, server_default=text("'tenant'")),
    )
    permission_version: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    source_version: str = Field(sa_column=Column(String(128), nullable=False))
    content_hash: str = Field(sa_column=Column(String(64), nullable=False))
    status: str = Field(
        default="active",
        sa_column=Column(String(32), nullable=False, server_default=text("'active'")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class RetrievalUnitModel(SQLModel, table=True):
    """资源的一个检索视图或知识分块。"""

    __tablename__ = "retrieval_unit"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            "index_generation",
            name="ux_retrieval_unit_tenant_generation",
        ),
        UniqueConstraint(
            "resource_id",
            "unit_key",
            "index_generation",
            name="ux_retrieval_unit_generation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "resource_id"],
            ["retrieval_resource.tenant_id", "retrieval_resource.id"],
            name="fk_retrieval_unit_resource_tenant",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'superseded', 'failed', 'tombstoned')",
            name="ck_retrieval_unit_status",
        ),
        Index(
            "ux_retrieval_unit_active",
            "resource_id",
            "unit_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "idx_retrieval_unit_lookup",
            "tenant_id",
            "index_generation",
            "status",
            "content_kind",
        ),
        Index(
            "idx_retrieval_unit_lexical",
            "lexical_vector",
            postgresql_using="gin",
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "idx_retrieval_unit_trgm",
            text("((title || ' ' || content || ' ' || contextual_text)) gin_trgm_ops"),
            postgresql_using="gin",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    tenant_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    resource_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    unit_key: str = Field(sa_column=Column(String(128), nullable=False))
    content_kind: str = Field(sa_column=Column(String(64), nullable=False))
    title: str = Field(sa_column=Column(Text, nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))
    contextual_text: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=text("''")))
    language: str | None = Field(default=None, sa_column=Column(String(16), nullable=True))
    unit_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    lexical_vector: str | None = Field(default=None, sa_column=Column(TSVECTOR, nullable=True))
    content_hash: str = Field(sa_column=Column(String(64), nullable=False))
    index_generation: str = Field(sa_column=Column(String(64), nullable=False))
    status: str = Field(
        default="pending",
        sa_column=Column(String(32), nullable=False, server_default=text("'pending'")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class RetrievalEmbeddingModel(SQLModel, table=True):
    """固定 1024 维 profile 的 generation 级向量。"""

    __tablename__ = "retrieval_embedding"
    __table_args__ = (
        UniqueConstraint(
            "unit_id",
            "embedding_profile",
            "index_generation",
            name="ux_retrieval_embedding_generation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "unit_id", "index_generation"],
            [
                "retrieval_unit.tenant_id",
                "retrieval_unit.id",
                "retrieval_unit.index_generation",
            ],
            name="fk_retrieval_embedding_unit_generation",
        ),
        CheckConstraint("dimension = 1024", name="ck_retrieval_embedding_dimension"),
        CheckConstraint(
            "status IN ('pending', 'active', 'superseded', 'failed')",
            name="ck_retrieval_embedding_status",
        ),
        CheckConstraint(
            "status <> 'active' OR embedding IS NOT NULL",
            name="ck_retrieval_embedding_active_vector",
        ),
        Index(
            "ux_retrieval_embedding_active",
            "unit_id",
            "embedding_profile",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "idx_retrieval_embedding_lookup",
            "tenant_id",
            "embedding_profile",
            "index_generation",
            "status",
        ),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    tenant_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    unit_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    embedding_profile: str = Field(sa_column=Column(String(64), nullable=False))
    provider: str = Field(sa_column=Column(String(64), nullable=False))
    model: str = Field(sa_column=Column(String(128), nullable=False))
    dimension: int = Field(default=1024, sa_column=Column(Integer, nullable=False, server_default=text("1024")))
    embedding: list[float] | None = Field(default=None, sa_column=Column(VECTOR(1024), nullable=True))
    text_hash: str = Field(sa_column=Column(String(64), nullable=False))
    index_generation: str = Field(sa_column=Column(String(64), nullable=False))
    status: str = Field(
        default="pending",
        sa_column=Column(String(32), nullable=False, server_default=text("'pending'")),
    )
    error_code: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class RetrievalIndexJobModel(SQLModel, table=True):
    """可重试、可幂等领取的索引任务。"""

    __tablename__ = "retrieval_index_job"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["retrieval_source.tenant_id", "retrieval_source.id"],
            name="fk_retrieval_index_job_source_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id", "resource_id"],
            [
                "retrieval_resource.tenant_id",
                "retrieval_resource.source_id",
                "retrieval_resource.id",
            ],
            name="fk_retrieval_index_job_resource_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id", "target_generation"],
            [
                "retrieval_index_generation.tenant_id",
                "retrieval_index_generation.source_id",
                "retrieval_index_generation.generation",
            ],
            name="fk_retrieval_index_job_generation",
        ),
        CheckConstraint("attempts >= 0", name="ck_retrieval_index_job_attempts"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_retrieval_index_job_status",
        ),
        Index(
            "ux_retrieval_index_job_source",
            "source_id",
            "operation",
            "target_generation",
            unique=True,
            postgresql_where=text("resource_id IS NULL"),
        ),
        Index(
            "ux_retrieval_index_job_resource",
            "source_id",
            "resource_id",
            "operation",
            "target_generation",
            unique=True,
            postgresql_where=text("resource_id IS NOT NULL"),
        ),
        Index("idx_retrieval_index_job_queue", "status", "available_at", "id"),
        Index("idx_retrieval_index_job_source", "tenant_id", "source_id", "status"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    tenant_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    source_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    resource_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    operation: str = Field(sa_column=Column(String(32), nullable=False))
    target_generation: str = Field(sa_column=Column(String(64), nullable=False))
    status: str = Field(
        default="pending",
        sa_column=Column(String(32), nullable=False, server_default=text("'pending'")),
    )
    attempts: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    error_code: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    available_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class RetrievalQueryTraceModel(SQLModel, table=True):
    """一次 profile 检索的可复现诊断记录。"""

    __tablename__ = "retrieval_query_trace"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "request_id",
            "profile",
            name="ux_retrieval_query_trace_request",
        ),
        CheckConstraint("latency_ms >= 0", name="ck_retrieval_query_trace_latency"),
        Index("idx_retrieval_query_trace_tenant", "tenant_id", "created_at"),
        Index(
            "idx_retrieval_query_trace_strategy",
            "profile",
            "strategy_version",
            "index_generation",
        ),
        Index("idx_retrieval_query_trace_error", "tenant_id", "error_code", "created_at"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    tenant_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    actor_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    request_id: str = Field(sa_column=Column(String(128), nullable=False))
    profile: str = Field(sa_column=Column(String(64), nullable=False))
    query_hash: str = Field(sa_column=Column(String(64), nullable=False))
    permission_version: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    scope_filters: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("scope", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    filters: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    channels: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    candidate_ranks: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    decision: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    strategy_version: str = Field(sa_column=Column(String(64), nullable=False))
    index_generation: str = Field(sa_column=Column(String(64), nullable=False))
    latency_ms: int = Field(sa_column=Column(Integer, nullable=False))
    error_code: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


__all__ = [
    "RetrievalEmbeddingModel",
    "RetrievalIndexGenerationModel",
    "RetrievalIndexJobModel",
    "RetrievalQueryTraceModel",
    "RetrievalResourceModel",
    "RetrievalSourceModel",
    "RetrievalUnitModel",
]
