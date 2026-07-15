"""remove legacy headless metric embedding storage

Revision ID: 083_remove_legacy_embedding
Revises: 082_retrieval_hybrid_p1
Create Date: 2026-07-15 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from pgvector.sqlalchemy import VECTOR

from alembic import op

revision = "083_remove_legacy_embedding"
down_revision = "082_retrieval_hybrid_p1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 统一检索表已经承接全部资产向量，旧指标专用表不再保留双写或回退入口。
    op.drop_table("headless_asset_embedding")


def downgrade() -> None:
    # 降级只恢复历史结构，不恢复已删除的旧向量数据。
    op.create_table(
        "headless_asset_embedding",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("asset_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("embedding_text", sa.Text(), nullable=False),
        sa.Column("embedding_text_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("embedding", VECTOR(), nullable=True),
        sa.Column("embedding_provider", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("embedding_model", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("embedding_dim", sa.BigInteger(), nullable=False),
        sa.Column("embedding_batch_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_headless_asset_embedding_asset",
        "headless_asset_embedding",
        ["oid", "dataset_id", "asset_type", "asset_id"],
        unique=True,
    )
    op.create_index(
        "idx_headless_asset_embedding_lookup",
        "headless_asset_embedding",
        ["oid", "dataset_id", "asset_type", "status"],
    )
