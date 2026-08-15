"""新增 ChatBI 用户记忆的独立会话计数和行为证据来源。"""

import sqlalchemy as sa
from alembic import op


revision = "105_chatbi_memory_behavior"
down_revision = "104_chatbi_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chatbi_memory",
        sa.Column(
            "session_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "chatbi_memory_evidence",
        sa.Column("source_session_id", sa.String(length=160), nullable=True),
    )
    op.create_index(
        "idx_chatbi_memory_evidence_session",
        "chatbi_memory_evidence",
        ["oid", "user_id", "memory_id", "source_session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_chatbi_memory_evidence_session",
        table_name="chatbi_memory_evidence",
    )
    op.drop_column("chatbi_memory_evidence", "source_session_id")
    op.drop_column("chatbi_memory", "session_count")
