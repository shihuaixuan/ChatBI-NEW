"""080_retrieval_storage_p1

Revision ID: 080_retrieval_storage_p1
Revises: 079_chatbi_agent_runtime
Create Date: 2026-07-14 00:00:00.000000

"""

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "080_retrieval_storage_p1"
down_revision = "079_chatbi_agent_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 旧环境可能只升级过部分向量表；统一存储仍显式保证扩展存在。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    op.create_table(
        "retrieval_source",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_key", sa.String(length=256), nullable=False),
        sa.Column("namespace", sa.String(length=128), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "acl_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("active_generation", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'rebuilding', 'failed', 'deleted')",
            name="ck_retrieval_source_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="ux_retrieval_source_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_type",
            "source_key",
            name="ux_retrieval_source_key",
        ),
    )
    op.create_index(
        "idx_retrieval_source_lookup",
        "retrieval_source",
        ["tenant_id", "source_type", "status"],
    )

    op.create_table(
        "retrieval_resource",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("namespace", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("source_resource_id", sa.String(length=256), nullable=False),
        sa.Column("parent_resource_id", sa.BigInteger(), nullable=True),
        sa.Column("dataset_id", sa.BigInteger(), nullable=True),
        sa.Column("knowledge_base_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "acl",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("visibility", sa.String(length=32), server_default=sa.text("'tenant'"), nullable=False),
        sa.Column("permission_version", sa.String(length=128), nullable=True),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "NOT (dataset_id IS NOT NULL AND knowledge_base_id IS NOT NULL)",
            name="ck_retrieval_resource_scope_exclusive",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'deleted')",
            name="ck_retrieval_resource_status",
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'tenant', 'public')",
            name="ck_retrieval_resource_visibility",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_resource_id"],
            ["retrieval_resource.tenant_id", "retrieval_resource.id"],
            name="fk_retrieval_resource_parent_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["retrieval_source.tenant_id", "retrieval_source.id"],
            name="fk_retrieval_resource_source_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="ux_retrieval_resource_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "id",
            name="ux_retrieval_resource_source_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "resource_type",
            "source_resource_id",
            name="ux_retrieval_resource_source_key",
        ),
    )
    op.create_index(
        "idx_retrieval_resource_dataset",
        "retrieval_resource",
        ["tenant_id", "namespace", "dataset_id", "resource_type", "status"],
    )
    op.create_index(
        "idx_retrieval_resource_knowledge",
        "retrieval_resource",
        ["tenant_id", "namespace", "knowledge_base_id", "resource_type", "status"],
    )
    op.create_index(
        "idx_retrieval_resource_source",
        "retrieval_resource",
        ["tenant_id", "source_id", "status"],
    )

    op.create_table(
        "retrieval_unit",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("unit_key", sa.String(length=128), nullable=False),
        sa.Column("content_kind", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("contextual_text", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("lexical_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("index_generation", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'superseded', 'failed', 'tombstoned')",
            name="ck_retrieval_unit_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "resource_id"],
            ["retrieval_resource.tenant_id", "retrieval_resource.id"],
            name="fk_retrieval_unit_resource_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "index_generation",
            name="ux_retrieval_unit_tenant_generation",
        ),
        sa.UniqueConstraint(
            "resource_id",
            "unit_key",
            "index_generation",
            name="ux_retrieval_unit_generation",
        ),
    )
    op.create_index(
        "ux_retrieval_unit_active",
        "retrieval_unit",
        ["resource_id", "unit_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "idx_retrieval_unit_lookup",
        "retrieval_unit",
        ["tenant_id", "index_generation", "status", "content_kind"],
    )
    op.create_index(
        "idx_retrieval_unit_lexical",
        "retrieval_unit",
        ["lexical_vector"],
        postgresql_using="gin",
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "retrieval_embedding",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("unit_id", sa.BigInteger(), nullable=False),
        sa.Column("embedding_profile", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("dimension", sa.Integer(), server_default=sa.text("1024"), nullable=False),
        sa.Column("embedding", VECTOR(1024), nullable=True),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("index_generation", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("dimension = 1024", name="ck_retrieval_embedding_dimension"),
        sa.CheckConstraint(
            "status <> 'active' OR embedding IS NOT NULL",
            name="ck_retrieval_embedding_active_vector",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'superseded', 'failed')",
            name="ck_retrieval_embedding_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "unit_id", "index_generation"],
            [
                "retrieval_unit.tenant_id",
                "retrieval_unit.id",
                "retrieval_unit.index_generation",
            ],
            name="fk_retrieval_embedding_unit_generation",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "unit_id",
            "embedding_profile",
            "index_generation",
            name="ux_retrieval_embedding_generation",
        ),
    )
    op.create_index(
        "ux_retrieval_embedding_active",
        "retrieval_embedding",
        ["unit_id", "embedding_profile"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "idx_retrieval_embedding_lookup",
        "retrieval_embedding",
        ["tenant_id", "embedding_profile", "index_generation", "status"],
    )

    op.create_table(
        "retrieval_index_job",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("target_generation", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempts >= 0", name="ck_retrieval_index_job_attempts"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_retrieval_index_job_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id", "resource_id"],
            [
                "retrieval_resource.tenant_id",
                "retrieval_resource.source_id",
                "retrieval_resource.id",
            ],
            name="fk_retrieval_index_job_resource_source",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["retrieval_source.tenant_id", "retrieval_source.id"],
            name="fk_retrieval_index_job_source_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_retrieval_index_job_source",
        "retrieval_index_job",
        ["source_id", "operation", "target_generation"],
        unique=True,
        postgresql_where=sa.text("resource_id IS NULL"),
    )
    op.create_index(
        "ux_retrieval_index_job_resource",
        "retrieval_index_job",
        ["source_id", "resource_id", "operation", "target_generation"],
        unique=True,
        postgresql_where=sa.text("resource_id IS NOT NULL"),
    )
    op.create_index(
        "idx_retrieval_index_job_queue",
        "retrieval_index_job",
        ["status", "available_at", "id"],
    )
    op.create_index(
        "idx_retrieval_index_job_source",
        "retrieval_index_job",
        ["tenant_id", "source_id", "status"],
    )

    op.create_table(
        "retrieval_query_trace",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("profile", sa.String(length=64), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("permission_version", sa.String(length=128), nullable=True),
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "filters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "channels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "candidate_ranks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "decision",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("index_generation", sa.String(length=64), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("latency_ms >= 0", name="ck_retrieval_query_trace_latency"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "request_id",
            "profile",
            name="ux_retrieval_query_trace_request",
        ),
    )
    op.create_index(
        "idx_retrieval_query_trace_tenant",
        "retrieval_query_trace",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "idx_retrieval_query_trace_strategy",
        "retrieval_query_trace",
        ["profile", "strategy_version", "index_generation"],
    )
    op.create_index(
        "idx_retrieval_query_trace_error",
        "retrieval_query_trace",
        ["tenant_id", "error_code", "created_at"],
    )


def downgrade() -> None:
    # 按外键依赖逆序删除；pgvector 仍被旧表使用，不能在此撤销扩展。
    op.drop_table("retrieval_query_trace")
    op.drop_table("retrieval_index_job")
    op.drop_table("retrieval_embedding")
    op.drop_table("retrieval_unit")
    op.drop_table("retrieval_resource")
    op.drop_table("retrieval_source")
