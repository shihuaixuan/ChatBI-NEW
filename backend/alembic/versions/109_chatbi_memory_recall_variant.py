"""为用户记忆使用记录增加召回灰度分组。"""

import sqlalchemy as sa
from alembic import op


revision = "109_chatbi_memory_recall_variant"
down_revision = "108_chatbi_memory_adoption"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chatbi_memory_usage",
        sa.Column(
            "recall_variant",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'disabled'"),
        ),
    )
    op.create_index(
        "idx_chatbi_memory_usage_variant_time",
        "chatbi_memory_usage",
        ["oid", "user_id", "recall_variant", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_chatbi_memory_usage_variant_time",
        table_name="chatbi_memory_usage",
    )
    op.drop_column("chatbi_memory_usage", "recall_variant")
