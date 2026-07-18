"""删除已由统一检索替代的语义索引表

Revision ID: 086_remove_legacy_sem_index
Revises: 085_active_question_flows
Create Date: 2026-07-17 00:00:00.000000
"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "086_remove_legacy_sem_index"
down_revision = "085_active_question_flows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 统一检索表已经承接资源、分块与向量，旧表不再保留重复写入入口。
    op.drop_table("headless_asset_document")
    op.drop_table("headless_schema_index")


def downgrade() -> None:
    # 降级只恢复历史结构，不恢复已删除的派生索引数据。
    op.create_table(
        "headless_schema_index",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "element_type",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
        ),
        sa.Column("element_id", sa.BigInteger(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_headless_schema_index_element",
        "headless_schema_index",
        ["oid", "dataset_id", "element_type", "element_id"],
        unique=True,
    )
    op.create_index(
        "idx_headless_schema_index_text",
        "headless_schema_index",
        ["oid", "dataset_id", "element_type"],
        unique=False,
    )

    op.create_table(
        "headless_asset_document",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "asset_type",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
        ),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "doc_key",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("business_text", sa.Text(), nullable=False),
        sa.Column("technical_text", sa.Text(), nullable=False),
        sa.Column("alias_text", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "index_version",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "embedding_status",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
        ),
        sa.Column(
            "embedding_ref",
            sqlmodel.sql.sqltypes.AutoString(length=256),
            nullable=True,
        ),
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
