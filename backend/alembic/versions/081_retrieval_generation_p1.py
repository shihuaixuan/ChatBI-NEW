"""081_retrieval_generation_p1

Revision ID: 081_retrieval_generation_p1
Revises: 080_retrieval_storage_p1
Create Date: 2026-07-14 16:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "081_retrieval_generation_p1"
down_revision = "080_retrieval_storage_p1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retrieval_index_generation",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.String(length=64), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("previous_generation", sa.String(length=64), nullable=True),
        sa.Column("embedding_profile", sa.String(length=64), nullable=False),
        sa.Column("embedding_provider", sa.String(length=64), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), server_default=sa.text("1024"), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'building'"), nullable=False),
        sa.Column("expected_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("succeeded_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("resource_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("unit_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("embedding_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('building', 'ready', 'active', 'superseded', 'failed', 'cancelled')",
            name="ck_retrieval_index_generation_status",
        ),
        sa.CheckConstraint(
            "expected_jobs >= 0 AND succeeded_jobs >= 0 AND failed_jobs >= 0",
            name="ck_retrieval_index_generation_job_counts",
        ),
        sa.CheckConstraint(
            "resource_count >= 0 AND unit_count >= 0 AND embedding_count >= 0",
            name="ck_retrieval_index_generation_asset_counts",
        ),
        sa.CheckConstraint(
            "embedding_dimension = 1024",
            name="ck_retrieval_index_generation_dimension",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["retrieval_source.tenant_id", "retrieval_source.id"],
            name="fk_retrieval_index_generation_source_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "generation",
            name="ux_retrieval_index_generation_key",
        ),
    )
    op.create_index(
        "ux_retrieval_index_generation_active",
        "retrieval_index_generation",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "idx_retrieval_index_generation_source",
        "retrieval_index_generation",
        ["tenant_id", "source_id", "status", "created_at"],
    )
    op.create_foreign_key(
        "fk_retrieval_index_job_generation",
        "retrieval_index_job",
        "retrieval_index_generation",
        ["tenant_id", "source_id", "target_generation"],
        ["tenant_id", "source_id", "generation"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_retrieval_index_job_generation",
        "retrieval_index_job",
        type_="foreignkey",
    )
    op.drop_index(
        "idx_retrieval_index_generation_source",
        table_name="retrieval_index_generation",
    )
    op.drop_index(
        "ux_retrieval_index_generation_active",
        table_name="retrieval_index_generation",
    )
    op.drop_table("retrieval_index_generation")
