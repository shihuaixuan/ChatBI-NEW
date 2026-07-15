"""078_graph_chat_history

Revision ID: 078_graph_chat_history
Revises: 077_headless_metric_embedding
Create Date: 2026-07-10 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "078_graph_chat_history"
down_revision = "077_headless_metric_embedding"
branch_labels = None
depends_on = None


def upgrade():
    # 先以可空列完成历史数据分类，再收紧非空约束，避免破坏既有会话记录。
    op.add_column("chat_record", sa.Column("execution_type", sa.String(length=32), nullable=True))
    op.execute(
        """
        UPDATE chat_record AS record
        SET execution_type = 'agentic'
        WHERE EXISTS (
            SELECT 1 FROM agentic_run AS run WHERE run.record_id = record.id
        )
        """
    )
    op.execute(
        "UPDATE chat_record SET execution_type = 'legacy' WHERE execution_type IS NULL"
    )
    op.alter_column("chat_record", "execution_type", nullable=False, server_default="legacy")

    # WorkflowRun 的执行归属只为后续显式写入预留，严禁从历史 JSON 推断回填。
    op.add_column("workflow_run", sa.Column("chat_id", sa.BigInteger(), nullable=True))
    op.add_column("workflow_run", sa.Column("record_id", sa.BigInteger(), nullable=True))
    # 交互式 Run 必须同时绑定 chat_id 和 record_id，独立 Run 则必须同时为空。
    op.create_check_constraint(
        "ck_workflow_run_chat_ownership",
        "workflow_run",
        "(chat_id IS NULL AND record_id IS NULL) "
        "OR (chat_id IS NOT NULL AND record_id IS NOT NULL)",
    )
    op.create_index(
        "idx_workflow_run_chat",
        "workflow_run",
        ["chat_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ux_workflow_run_record",
        "workflow_run",
        ["record_id"],
        unique=True,
    )

    # 会话删除只登记可重试清理任务，Artifact 正文由后续异步流程处理。
    op.create_table("workflow_artifact_cleanup",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("storage_uri", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id"),
    )
    op.create_index(
        "idx_workflow_artifact_cleanup_status",
        "workflow_artifact_cleanup",
        ["status", "updated_at"],
    )


def downgrade():
    # 按依赖逆序删除索引、表和列，确保降级过程不会引用已删除对象。
    op.drop_index("idx_workflow_artifact_cleanup_status", table_name="workflow_artifact_cleanup")
    op.drop_table("workflow_artifact_cleanup")
    op.drop_index("ux_workflow_run_record", table_name="workflow_run")
    op.drop_index("idx_workflow_run_chat", table_name="workflow_run")
    op.drop_column("workflow_run", "record_id")
    op.drop_column("workflow_run", "chat_id")
    op.drop_column("chat_record", "execution_type")
