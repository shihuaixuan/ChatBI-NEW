"""077_headless_metric_embedding

Revision ID: 077_headless_metric_embedding
Revises: 076_chat_headless_dataset
Create Date: 2026-07-01 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from pgvector.sqlalchemy import VECTOR

from alembic import op

revision = "077_headless_metric_embedding"
down_revision = "076_chat_headless_dataset"
branch_labels = None
depends_on = None


def upgrade():
    # 确保 pgvector 扩展存在，避免新环境首次创建向量表失败。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

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


def downgrade():
    op.drop_index("idx_headless_asset_embedding_lookup", table_name="headless_asset_embedding")
    op.drop_index("ux_headless_asset_embedding_asset", table_name="headless_asset_embedding")
    op.drop_table("headless_asset_embedding")
