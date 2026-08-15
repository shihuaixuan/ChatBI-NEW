"""为用户记忆使用记录增加采用判定。"""

import sqlalchemy as sa
from alembic import op


revision = "108_chatbi_memory_adoption"
down_revision = "107_chatbi_memory_profile_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chatbi_memory_usage",
        sa.Column("adopted", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "chatbi_memory_usage",
        sa.Column("adopted_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chatbi_memory_usage", "adopted_at")
    op.drop_column("chatbi_memory_usage", "adopted")
