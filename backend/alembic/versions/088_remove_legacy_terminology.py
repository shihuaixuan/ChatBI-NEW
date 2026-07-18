"""删除已由 Semantic 替代的旧术语表

Revision ID: 088_remove_legacy_terminology
Revises: 087_sem_term_dataset_scope
Create Date: 2026-07-18 00:00:00.000000
"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "088_remove_legacy_terminology"
down_revision = "087_sem_term_dataset_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 旧表必须先通过迁移脚本完成迁移和清理，避免部署升级时丢失数据。
    remaining_count = (
        op.get_bind().execute(sa.text("SELECT COUNT(*) FROM terminology")).scalar_one()
    )
    if remaining_count:
        raise RuntimeError(
            "旧 terminology 表仍有 "
            f"{remaining_count} 条记录；请先运行迁移脚本预检，并使用 "
            "--apply --purge-source 完成迁移和源数据清理"
        )
    op.drop_table("terminology")


def downgrade() -> None:
    # 降级只恢复旧表结构，不恢复已迁移或清理的历史数据。
    op.create_table(
        "terminology",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=True),
        sa.Column("pid", sa.BigInteger(), nullable=True),
        sa.Column("create_time", sa.DateTime(), nullable=True),
        sa.Column(
            "word",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("embedding", VECTOR(), nullable=True),
        sa.Column(
            "aliases",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "dataset_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "mapped_assets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("specific_ds", sa.Boolean(), nullable=True),
        sa.Column(
            "datasource_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
