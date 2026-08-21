"""记录语义数据集的已发布契约版本快照。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "118_semantic_contract_versions"
down_revision = "117_semantic_contract_phase2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "headless_semantic_contract_version",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("contract_version", sa.BigInteger(), nullable=False),
        sa.Column("schema_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "asset_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("published_by", sa.BigInteger(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_headless_semantic_contract_version",
        "headless_semantic_contract_version",
        ["oid", "dataset_id", "contract_version"],
        unique=True,
    )
    op.create_index(
        "idx_headless_semantic_contract_version_dataset",
        "headless_semantic_contract_version",
        ["oid", "dataset_id", "published_at"],
    )
    # 为迁移前已经发布的数据集保留当前版本的可追溯起点。
    op.execute(
        sa.text(
            "INSERT INTO headless_semantic_contract_version "
            "(oid, dataset_id, schema_version, contract_version, "
            "schema_fingerprint, asset_snapshot, published_at) "
            "SELECT oid, id, schema_version, contract_version, "
            "'LEGACY_BACKFILL', '{}'::jsonb, CURRENT_TIMESTAMP "
            "FROM headless_dataset WHERE contract_version > 0"
        )
    )


def downgrade() -> None:
    op.drop_index(
        "idx_headless_semantic_contract_version_dataset",
        table_name="headless_semantic_contract_version",
    )
    op.drop_index(
        "ux_headless_semantic_contract_version",
        table_name="headless_semantic_contract_version",
    )
    op.drop_table("headless_semantic_contract_version")
