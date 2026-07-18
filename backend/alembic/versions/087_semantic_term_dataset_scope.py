"""增加 Semantic 术语的数据集适用范围

Revision ID: 087_sem_term_dataset_scope
Revises: 086_remove_legacy_sem_index
Create Date: 2026-07-18 00:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "087_sem_term_dataset_scope"
down_revision = "086_remove_legacy_sem_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 空数组表示术语适用于所属主题域中的全部数据集。
    op.add_column(
        "headless_term",
        sa.Column(
            "related_datasets",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("headless_term", "related_datasets")
