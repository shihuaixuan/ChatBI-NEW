"""删除 Agent Event 与 ChatRecord 的旧兼容结构。

Revision ID: 096_remove_agent_event_compat
Revises: 095_chat_record_run_id
"""

import sqlalchemy as sa

from alembic import op

revision = "096_remove_agent_event_compat"
down_revision = "095_chat_record_run_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "ux_chatbi_agent_trace_sequence",
        table_name="chatbi_agent_trace_event",
    )
    op.rename_table("chatbi_agent_trace_event", "chatbi_agent_event")
    op.create_index(
        "ux_chatbi_agent_event_sequence",
        "chatbi_agent_event",
        ["run_id", "sequence"],
        unique=True,
    )
    op.drop_column("chat_record", "trace_id")


def downgrade() -> None:
    op.add_column(
        "chat_record",
        sa.Column("trace_id", sa.String(length=64), nullable=True),
    )
    op.execute("UPDATE chat_record SET trace_id = run_id WHERE run_id IS NOT NULL")
    op.drop_index(
        "ux_chatbi_agent_event_sequence",
        table_name="chatbi_agent_event",
    )
    op.rename_table("chatbi_agent_event", "chatbi_agent_trace_event")
    op.create_index(
        "ux_chatbi_agent_trace_sequence",
        "chatbi_agent_trace_event",
        ["run_id", "sequence"],
        unique=True,
    )
