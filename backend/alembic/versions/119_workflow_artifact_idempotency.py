"""为结果 Artifact 增加持久化幂等键。"""

import sqlalchemy as sa

from alembic import op

revision = "119_workflow_artifact_idempotency"
down_revision = "118_semantic_contract_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_artifact",
        sa.Column("idempotency_key", sa.String(length=256), nullable=True),
    )
    # 旧版本可能跨进程重复写入同一键；只选择最早一条作为可恢复记录，
    # 其余历史 Artifact 保留但不参与新的唯一约束。
    op.execute(
        """
        WITH ranked AS (
            SELECT artifact_id,
                   metadata_json ->> 'idempotency_key' AS idempotency_key,
                   row_number() OVER (
                       PARTITION BY run_id, kind,
                                    metadata_json ->> 'idempotency_key'
                       ORDER BY created_at, artifact_id
                   ) AS sequence
            FROM workflow_artifact
            WHERE metadata_json ? 'idempotency_key'
        )
        UPDATE workflow_artifact AS artifact
        SET idempotency_key = ranked.idempotency_key
        FROM ranked
        WHERE artifact.artifact_id = ranked.artifact_id
          AND ranked.sequence = 1
        """
    )
    op.create_index(
        "ux_workflow_artifact_idempotency",
        "workflow_artifact",
        ["run_id", "kind", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_workflow_artifact_idempotency",
        table_name="workflow_artifact",
    )
    op.drop_column("workflow_artifact", "idempotency_key")
