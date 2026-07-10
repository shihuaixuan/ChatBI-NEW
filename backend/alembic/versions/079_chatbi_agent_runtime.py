"""079_chatbi_agent_runtime

Revision ID: 079_chatbi_agent_runtime
Revises: 078_graph_chat_history
Create Date: 2026-07-10 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "079_chatbi_agent_runtime"
down_revision = "078_graph_chat_history"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chatbi_agent_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="created", nullable=False),
        sa.Column("messages", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("budget_snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_class", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_chatbi_agent_run_record", "chatbi_agent_run", ["record_id"], unique=False)
    op.create_index("idx_chatbi_agent_run_chat", "chatbi_agent_run", ["chat_id", sa.text("created_at DESC")], unique=False)
    op.create_index("idx_chatbi_agent_run_status", "chatbi_agent_run", ["oid", "status", sa.text("updated_at DESC")], unique=False)

    op.create_table(
        "chatbi_agent_step",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("tool_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column("args_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="running", nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("token_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_chatbi_agent_step_index", "chatbi_agent_step", ["run_id", "step_index"], unique=True)
    op.create_index("idx_chatbi_agent_step_run", "chatbi_agent_step", ["run_id", "created_at"], unique=False)

    op.create_table(
        "chatbi_agent_trace_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("step_id", sa.BigInteger(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_chatbi_agent_trace_sequence", "chatbi_agent_trace_event", ["run_id", "sequence"], unique=True)

    op.create_table(
        "chatbi_agent_clarification",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="pending", nullable=False),
        sa.Column("tool_call_id", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_chatbi_agent_clarification_record", "chatbi_agent_clarification", ["record_id", "status"], unique=False)
    op.create_index(
        "ux_chatbi_agent_clarification_pending",
        "chatbi_agent_clarification",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade():
    op.drop_table("chatbi_agent_clarification")
    op.drop_table("chatbi_agent_trace_event")
    op.drop_table("chatbi_agent_step")
    op.drop_table("chatbi_agent_run")
