"""068_agentic_chat_flow

Revision ID: 068_agentic_chat_flow
Revises: 067_semantic_metric_dimension
Create Date: 2026-05-26 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "068_agentic_chat_flow"
down_revision = "067_semantic_metric_dimension"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agentic_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="created", nullable=False),
        sa.Column("mode", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="agentic_chatbi", nullable=False),
        sa.Column("current_step", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_agentic_run_record", "agentic_run", ["record_id"], unique=False)
    op.create_index("idx_agentic_run_chat", "agentic_run", ["chat_id", sa.text("created_at DESC")], unique=False)
    op.create_index("idx_agentic_run_status", "agentic_run", ["oid", "status", sa.text("updated_at DESC")], unique=False)

    op.create_table(
        "agentic_step",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("tool_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column("strategy", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("input_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("output_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="running", nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("token_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_agentic_step_index", "agentic_step", ["run_id", "step_index"], unique=True)
    op.create_index("idx_agentic_step_run", "agentic_step", ["run_id", "created_at"], unique=False)

    op.create_table(
        "agentic_clarification",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="pending", nullable=False),
        sa.Column("target_slots", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_agentic_clarification_run", "agentic_clarification", ["run_id", sa.text("created_at DESC")], unique=False)
    op.create_index("idx_agentic_clarification_record", "agentic_clarification", ["record_id", "status"], unique=False)
    op.create_index(
        "ux_agentic_clarification_pending",
        "agentic_clarification",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "agentic_trace_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("step_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("public_payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("private_payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_agentic_trace_event_run", "agentic_trace_event", ["run_id", "created_at"], unique=False)

    op.add_column("chat_record", sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True))
    op.add_column("chat_record", sa.Column("trace_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True))
    op.create_index("idx_chat_record_status", "chat_record", ["status"], unique=False)
    op.execute(
        """
        UPDATE chat_record
        SET status = CASE
          WHEN finish IS TRUE THEN 'finished'
          WHEN error IS NOT NULL AND error <> '' THEN 'failed'
          ELSE 'created'
        END
        WHERE status IS NULL
        """
    )


def downgrade():
    op.drop_index("idx_chat_record_status", table_name="chat_record")
    op.drop_column("chat_record", "trace_id")
    op.drop_column("chat_record", "status")

    op.drop_index("idx_agentic_trace_event_run", table_name="agentic_trace_event")
    op.drop_table("agentic_trace_event")

    op.drop_index("ux_agentic_clarification_pending", table_name="agentic_clarification")
    op.drop_index("idx_agentic_clarification_record", table_name="agentic_clarification")
    op.drop_index("idx_agentic_clarification_run", table_name="agentic_clarification")
    op.drop_table("agentic_clarification")

    op.drop_index("idx_agentic_step_run", table_name="agentic_step")
    op.drop_index("ux_agentic_step_index", table_name="agentic_step")
    op.drop_table("agentic_step")

    op.drop_index("idx_agentic_run_status", table_name="agentic_run")
    op.drop_index("idx_agentic_run_chat", table_name="agentic_run")
    op.drop_index("idx_agentic_run_record", table_name="agentic_run")
    op.drop_table("agentic_run")
