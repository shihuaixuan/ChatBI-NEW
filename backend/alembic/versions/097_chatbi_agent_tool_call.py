"""新增 Agent Tool Call 独立事实表。

Revision ID: 097_chatbi_agent_tool_call
Revises: 096_remove_agent_event_compat
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "097_chatbi_agent_tool_call"
down_revision = "096_remove_agent_event_compat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chatbi_agent_tool_call",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("step_id", sa.BigInteger(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "args_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "result_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_chatbi_agent_tool_call_id",
        "chatbi_agent_tool_call",
        ["run_id", "tool_call_id"],
        unique=True,
    )
    op.create_index(
        "idx_chatbi_agent_tool_call_run_step",
        "chatbi_agent_tool_call",
        ["run_id", "step_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_chatbi_agent_tool_call_run_step",
        table_name="chatbi_agent_tool_call",
    )
    op.drop_index(
        "ux_chatbi_agent_tool_call_id",
        table_name="chatbi_agent_tool_call",
    )
    op.drop_table("chatbi_agent_tool_call")
