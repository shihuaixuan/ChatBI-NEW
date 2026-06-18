"""073_headless_asset_relation_document

Revision ID: 073_headless_asset_doc
Revises: 072_headless_asset_p0
Create Date: 2026-06-03 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "073_headless_asset_doc"
down_revision = "072_headless_asset_p0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "headless_asset_alias",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("asset_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("alias_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("language", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_headless_asset_alias_lookup", "headless_asset_alias", ["oid", "asset_type", "asset_id", "status"])
    op.create_index("idx_headless_asset_alias_text", "headless_asset_alias", ["oid", "alias", "status"])

    op.create_table(
        "headless_asset_relation",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("source_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("relation_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("target_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("weight", sa.Float(), server_default=sa.text("1.0"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_headless_asset_relation_source",
        "headless_asset_relation",
        ["oid", "source_type", "source_id", "relation_type", "status"],
    )
    op.create_index(
        "idx_headless_asset_relation_target",
        "headless_asset_relation",
        ["oid", "target_type", "target_id", "relation_type", "status"],
    )

    op.create_table(
        "headless_asset_document",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("asset_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column("doc_key", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("business_text", sa.Text(), nullable=False),
        sa.Column("technical_text", sa.Text(), nullable=False),
        sa.Column("alias_text", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("index_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("embedding_status", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("embedding_ref", sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_headless_asset_document",
        "headless_asset_document",
        ["oid", "dataset_id", "asset_type", "asset_id"],
        unique=True,
    )
    op.create_index(
        "idx_headless_asset_document_dataset",
        "headless_asset_document",
        ["oid", "dataset_id", "index_version"],
    )


def downgrade():
    op.drop_index("idx_headless_asset_document_dataset", table_name="headless_asset_document")
    op.drop_index("ux_headless_asset_document", table_name="headless_asset_document")
    op.drop_table("headless_asset_document")

    op.drop_index("idx_headless_asset_relation_target", table_name="headless_asset_relation")
    op.drop_index("idx_headless_asset_relation_source", table_name="headless_asset_relation")
    op.drop_table("headless_asset_relation")

    op.drop_index("idx_headless_asset_alias_text", table_name="headless_asset_alias")
    op.drop_index("idx_headless_asset_alias_lookup", table_name="headless_asset_alias")
    op.drop_table("headless_asset_alias")
