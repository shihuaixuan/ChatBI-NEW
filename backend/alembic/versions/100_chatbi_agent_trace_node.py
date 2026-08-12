"""新增 ChatBI Agent 持久化 Trace 节点。

Revision ID: 100_chatbi_agent_trace_node
Revises: 099_agent_run_temporal_context
"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "100_chatbi_agent_trace_node"
down_revision = "099_agent_run_temporal_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chatbi_agent_trace_node",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "node_key",
            sqlmodel.sql.sqltypes.AutoString(length=160),
            nullable=False,
        ),
        sa.Column(
            "node_type",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
        ),
        sa.Column(
            "name",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sqlmodel.sql.sqltypes.AutoString(length=160),
            nullable=False,
        ),
        sa.Column(
            "status",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "input_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "output_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "input_artifact_ref",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "output_artifact_ref",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "state_diff",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "token_usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "error_code",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=True,
        ),
        sa.Column(
            "error_category",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "trace_id",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=True,
        ),
        sa.Column(
            "span_id",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_chatbi_agent_trace_node_key",
        "chatbi_agent_trace_node",
        ["run_id", "node_key"],
        unique=True,
    )
    op.create_index(
        "ux_chatbi_agent_trace_sequence",
        "chatbi_agent_trace_node",
        ["run_id", "sequence"],
        unique=True,
    )
    op.create_index(
        "ux_chatbi_agent_trace_root",
        "chatbi_agent_trace_node",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "idx_chatbi_agent_trace_parent",
        "chatbi_agent_trace_node",
        ["run_id", "parent_id", "sequence"],
        unique=False,
    )
    op.create_index(
        "idx_chatbi_agent_trace_started",
        "chatbi_agent_trace_node",
        ["run_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("chatbi_agent_trace_node")
