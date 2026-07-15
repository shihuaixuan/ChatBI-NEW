"""082_retrieval_hybrid_p1

Revision ID: 082_retrieval_hybrid_p1
Revises: 081_retrieval_generation_p1
Create Date: 2026-07-14 20:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "082_retrieval_hybrid_p1"
down_revision = "081_retrieval_generation_p1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 连续中文没有可靠空格边界，首版词法通道使用 PostgreSQL 原生字符 trigram。
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    op.create_index(
        "idx_retrieval_resource_title_trgm",
        "retrieval_resource",
        [sa.text("title gin_trgm_ops")],
        unique=False,
        postgresql_using="gin",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "idx_retrieval_unit_trgm",
        "retrieval_unit",
        [sa.text("((title || ' ' || content || ' ' || contextual_text)) gin_trgm_ops")],
        unique=False,
        postgresql_using="gin",
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_retrieval_unit_trgm",
        table_name="retrieval_unit",
        postgresql_using="gin",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_index(
        "idx_retrieval_resource_title_trgm",
        table_name="retrieval_resource",
        postgresql_using="gin",
        postgresql_where=sa.text("status = 'active'"),
    )
    # pg_trgm 可能被其他模块共用，降级只删除本模块索引，不删除扩展。
