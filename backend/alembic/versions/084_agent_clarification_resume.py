"""add explicit agent clarification resume checkpoint

Revision ID: 084_agent_clarification_resume
Revises: 083_remove_legacy_embedding
Create Date: 2026-07-15 00:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "084_agent_clarification_resume"
down_revision = "083_remove_legacy_embedding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 恢复点成为澄清记录的一等契约，不再通过 tool_call_id 是否为空推断流程来源。
    op.add_column(
        "chatbi_agent_clarification",
        sa.Column("resume_kind", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "chatbi_agent_clarification",
        sa.Column(
            "resume_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE chatbi_agent_clarification
        SET resume_kind = CASE
            WHEN tool_call_id IS NULL THEN 'question_understanding'
            ELSE 'agent_tool'
        END
        """
    )
    op.alter_column("chatbi_agent_clarification", "resume_kind", nullable=False)


def downgrade() -> None:
    op.drop_column("chatbi_agent_clarification", "resume_payload")
    op.drop_column("chatbi_agent_clarification", "resume_kind")
