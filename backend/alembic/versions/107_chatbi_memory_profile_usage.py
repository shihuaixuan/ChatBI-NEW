"""新增 ChatBI 用户画像版本和记忆使用记录。"""

import sqlalchemy as sa
from alembic import op


revision = "107_chatbi_memory_profile_usage"
down_revision = "106_chatbi_memory_embedding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chatbi_memory",
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_table(
        "chatbi_memory_usage",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("memory_id", sa.BigInteger(), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.String(length=160), nullable=True),
        sa.Column("run_id", sa.String(length=160), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("matched_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["chatbi_memory.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_chatbi_memory_usage_user_time",
        "chatbi_memory_usage",
        ["oid", "user_id", "created_at"],
    )
    op.create_index(
        "idx_chatbi_memory_usage_memory_time",
        "chatbi_memory_usage",
        ["memory_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_chatbi_memory_usage_memory_time",
        table_name="chatbi_memory_usage",
    )
    op.drop_index(
        "idx_chatbi_memory_usage_user_time",
        table_name="chatbi_memory_usage",
    )
    op.drop_table("chatbi_memory_usage")
    op.drop_column("chatbi_memory", "version")
