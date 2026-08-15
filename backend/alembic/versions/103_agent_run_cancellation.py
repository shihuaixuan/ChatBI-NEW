"""为 ChatBI Agent Run 增加取消请求和取消收口信息。"""

import sqlalchemy as sa

from alembic import op

revision = "103_agent_run_cancellation"
down_revision = "102_repair_semantic_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chatbi_agent_run",
        sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "chatbi_agent_run",
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "chatbi_agent_run",
        sa.Column("cancel_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "chatbi_agent_run",
        sa.Column("cancel_stage", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chatbi_agent_run",
        sa.Column("cancel_request_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "idx_chatbi_agent_run_cancel_requested",
        "chatbi_agent_run",
        ["status", "cancel_requested_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_chatbi_agent_run_cancel_requested",
        table_name="chatbi_agent_run",
    )
    op.drop_column("chatbi_agent_run", "cancel_request_id")
    op.drop_column("chatbi_agent_run", "cancel_stage")
    op.drop_column("chatbi_agent_run", "cancel_reason")
    op.drop_column("chatbi_agent_run", "cancelled_at")
    op.drop_column("chatbi_agent_run", "cancel_requested_at")
