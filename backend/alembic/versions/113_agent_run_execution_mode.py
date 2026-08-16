"""为 Agent Run 增加三模式编排所需的 execution_mode。"""

import sqlalchemy as sa

from alembic import op

revision = "113_agent_run_execution_mode"
down_revision = "110_verified_query_upgrade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chatbi_agent_run",
        sa.Column(
            "execution_mode",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'react_legacy'"),
        ),
    )
    op.create_index(
        "idx_chatbi_agent_run_execution_mode",
        "chatbi_agent_run",
        ["execution_mode"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_chatbi_agent_run_execution_mode",
        table_name="chatbi_agent_run",
    )
    op.drop_column("chatbi_agent_run", "execution_mode")
