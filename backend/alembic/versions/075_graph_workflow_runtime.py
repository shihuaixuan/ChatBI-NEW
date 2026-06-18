"""075_graph_workflow_runtime

Revision ID: 075_graph_workflow_runtime
Revises: 074_headless_asset_gov
Create Date: 2026-06-18 00:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "075_graph_workflow_runtime"
down_revision = "074_headless_asset_gov"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_definition",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("digest", sa.String(length=128), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="ux_workflow_definition_version"),
    )
    op.create_index("idx_workflow_definition_active", "workflow_definition", ["name", "active"])

    op.create_table(
        "workflow_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("definition_name", sa.String(length=128), nullable=False),
        sa.Column("definition_version", sa.String(length=64), nullable=False),
        sa.Column("definition_digest", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_node", sa.String(length=128), nullable=True),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index("idx_workflow_run_status", "workflow_run", ["oid", "status", sa.text("updated_at DESC")])
    op.create_index("idx_workflow_run_definition", "workflow_run", ["definition_name", "definition_version"])
    op.create_index("idx_workflow_run_request", "workflow_run", ["request_id"])

    op.create_table(
        "node_execution",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.String(length=128), nullable=False),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("handler", sa.String(length=256), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("output_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("route_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "node_name", "attempt", name="ux_node_execution_attempt"),
    )
    op.create_index("idx_node_execution_run", "node_execution", ["run_id", "sequence"])
    op.create_index("idx_node_execution_status", "node_execution", ["status"])

    op.create_table(
        "workflow_checkpoint",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.String(length=128), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("definition_digest", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checkpoint_id"),
        sa.UniqueConstraint("run_id", "sequence", name="ux_workflow_checkpoint_sequence"),
    )
    op.create_index("idx_workflow_checkpoint_run", "workflow_checkpoint", ["run_id", sa.text("sequence DESC")])

    op.create_table(
        "workflow_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("node_name", sa.String(length=128), nullable=True),
        sa.Column("node_execution_id", sa.String(length=64), nullable=True),
        sa.Column("public_payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("internal_payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("internal_payload_ref", sa.String(length=256), nullable=True),
        sa.Column("publish_status", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("publish_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint("run_id", "sequence", name="ux_workflow_event_sequence"),
    )
    op.create_index("idx_workflow_event_run", "workflow_event", ["run_id", "sequence"])
    op.create_index("idx_workflow_event_publish", "workflow_event", ["publish_status", "sequence"])

    op.create_table(
        "interaction_request",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("interaction_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("node_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("response_schema", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("allowed_update_paths", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("interaction_id"),
    )
    op.create_index("idx_interaction_request_run", "interaction_request", ["run_id", sa.text("created_at DESC")])
    op.create_index("idx_interaction_request_pending", "interaction_request", ["run_id", "status"])

    op.create_table(
        "workflow_artifact",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("digest", sa.String(length=128), nullable=False),
        sa.Column("storage_uri", sa.String(length=512), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("temporary", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id"),
    )
    op.create_index("idx_workflow_artifact_run", "workflow_artifact", ["run_id", "kind"])
    op.create_index("idx_workflow_artifact_temporary", "workflow_artifact", ["temporary", "created_at"])


def downgrade():
    op.drop_index("idx_workflow_artifact_temporary", table_name="workflow_artifact")
    op.drop_index("idx_workflow_artifact_run", table_name="workflow_artifact")
    op.drop_table("workflow_artifact")

    op.drop_index("idx_interaction_request_pending", table_name="interaction_request")
    op.drop_index("idx_interaction_request_run", table_name="interaction_request")
    op.drop_table("interaction_request")

    op.drop_index("idx_workflow_event_publish", table_name="workflow_event")
    op.drop_index("idx_workflow_event_run", table_name="workflow_event")
    op.drop_table("workflow_event")

    op.drop_index("idx_workflow_checkpoint_run", table_name="workflow_checkpoint")
    op.drop_table("workflow_checkpoint")

    op.drop_index("idx_node_execution_status", table_name="node_execution")
    op.drop_index("idx_node_execution_run", table_name="node_execution")
    op.drop_table("node_execution")

    op.drop_index("idx_workflow_run_request", table_name="workflow_run")
    op.drop_index("idx_workflow_run_definition", table_name="workflow_run")
    op.drop_index("idx_workflow_run_status", table_name="workflow_run")
    op.drop_table("workflow_run")

    op.drop_index("idx_workflow_definition_active", table_name="workflow_definition")
    op.drop_table("workflow_definition")
