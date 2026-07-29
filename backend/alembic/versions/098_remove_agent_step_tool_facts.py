"""删除 Step 上已经迁移到 Tool Call 的旧工具事实字段。

Revision ID: 098_remove_agent_step_tool_facts
Revises: 097_chatbi_agent_tool_call
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "098_remove_agent_step_tool_facts"
down_revision = "097_chatbi_agent_tool_call"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("chatbi_agent_step", "args_summary")
    op.drop_column("chatbi_agent_step", "tool_name")


def downgrade() -> None:
    op.add_column(
        "chatbi_agent_step",
        sa.Column("tool_name", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "chatbi_agent_step",
        sa.Column(
            "args_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
