"""为 ChatRecord 增加明确的 execution run 标识。

Revision ID: 095_chat_record_run_id
Revises: 094_sql_example_verification
"""

import sqlalchemy as sa

from alembic import op

revision = "095_chat_record_run_id"
down_revision = "094_sql_example_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_record",
        sa.Column("run_id", sa.String(length=64), nullable=True),
    )
    # 旧 trace_id 实际保存的是产品 execution run ID，先完整回填再切换读路径。
    op.execute("UPDATE chat_record SET run_id = trace_id WHERE trace_id IS NOT NULL")


def downgrade() -> None:
    op.drop_column("chat_record", "run_id")
