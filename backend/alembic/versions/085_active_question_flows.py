"""只保留 Graph 与 Agent 问数链路

Revision ID: 085_active_question_flows
Revises: 084_agent_clarification_resume
Create Date: 2026-07-17 00:00:00.000000
"""

from alembic import op

revision = "085_active_question_flows"
down_revision = "084_agent_clarification_resume"
branch_labels = None
depends_on = None


def upgrade():
    # 只修改新记录默认值；历史 legacy/agentic 数据保留用于只读展示和审计。
    op.alter_column("chat_record", "execution_type", server_default="graph")


def downgrade():
    op.alter_column("chat_record", "execution_type", server_default="legacy")
