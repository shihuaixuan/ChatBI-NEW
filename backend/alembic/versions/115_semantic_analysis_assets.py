"""新增维度层级和指标关系正式语义资产。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "115_semantic_analysis_assets"
down_revision = "114_dataset_instructions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 正式资产上线后，旧的运行时治理配置不再作为事实源。
    op.execute(
        sa.text(
            "UPDATE headless_dataset "
            "SET query_config = query_config - 'dimension_hierarchies' - 'research_relationships' "
            "WHERE query_config ? 'dimension_hierarchies' OR query_config ? 'research_relationships'"
        )
    )
    # 数据集级分析资产不属于具体模型，因此允许 model_id 为空。
    op.add_column(
        "headless_dataset",
        sa.Column("contract_version", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.alter_column(
        "headless_dataset_asset",
        "model_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.create_table(
        "headless_dimension_hierarchy",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("biz_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("hierarchy_type", sa.String(length=32), nullable=False, server_default=sa.text("'FIXED_LEVEL'")),
        sa.Column("contract_status", sa.String(length=32), nullable=False, server_default=sa.text("'DRAFT'")),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_headless_dimension_hierarchy_biz_name",
        "headless_dimension_hierarchy",
        ["oid", "domain_id", "biz_name"],
        unique=True,
    )
    op.create_index(
        "idx_headless_dimension_hierarchy_status",
        "headless_dimension_hierarchy",
        ["oid", "domain_id", "status"],
    )
    op.create_table(
        "headless_dimension_hierarchy_level",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("hierarchy_id", sa.BigInteger(), nullable=False),
        sa.Column("logical_dimension_id", sa.BigInteger(), nullable=False),
        sa.Column("level_order", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_headless_dimension_hierarchy_level_order",
        "headless_dimension_hierarchy_level",
        ["hierarchy_id", "level_order"],
        unique=True,
    )
    op.create_index(
        "ux_headless_dimension_hierarchy_level_dimension",
        "headless_dimension_hierarchy_level",
        ["hierarchy_id", "logical_dimension_id"],
        unique=True,
    )
    op.create_table(
        "headless_metric_relationship",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("target_metric_id", sa.BigInteger(), nullable=False),
        sa.Column("driver_metric_id", sa.BigInteger(), nullable=False),
        sa.Column("relationship_type", sa.String(length=32), nullable=False),
        sa.Column("validation_method", sa.String(length=32), nullable=False),
        sa.Column("expected_direction", sa.String(length=16), nullable=False, server_default=sa.text("'UNKNOWN'")),
        sa.Column("supported_time_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("relation_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("contract_status", sa.String(length=32), nullable=False, server_default=sa.text("'DRAFT'")),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_headless_metric_relationship_pair",
        "headless_metric_relationship",
        ["oid", "target_metric_id", "driver_metric_id"],
        unique=True,
    )
    op.create_index(
        "idx_headless_metric_relationship_status",
        "headless_metric_relationship",
        ["oid", "domain_id", "status"],
    )
    op.create_table(
        "headless_metric_relationship_dimension",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("relationship_id", sa.BigInteger(), nullable=False),
        sa.Column("logical_dimension_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_headless_metric_relationship_dimension",
        "headless_metric_relationship_dimension",
        ["relationship_id", "logical_dimension_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_column("headless_dataset", "contract_version")
    op.drop_index(
        "ux_headless_metric_relationship_dimension",
        table_name="headless_metric_relationship_dimension",
    )
    op.drop_table("headless_metric_relationship_dimension")
    op.drop_index(
        "idx_headless_metric_relationship_status",
        table_name="headless_metric_relationship",
    )
    op.drop_index(
        "ux_headless_metric_relationship_pair",
        table_name="headless_metric_relationship",
    )
    op.drop_table("headless_metric_relationship")
    op.drop_index(
        "ux_headless_dimension_hierarchy_level_dimension",
        table_name="headless_dimension_hierarchy_level",
    )
    op.drop_index(
        "ux_headless_dimension_hierarchy_level_order",
        table_name="headless_dimension_hierarchy_level",
    )
    op.drop_table("headless_dimension_hierarchy_level")
    op.drop_index(
        "idx_headless_dimension_hierarchy_status",
        table_name="headless_dimension_hierarchy",
    )
    op.drop_index(
        "ux_headless_dimension_hierarchy_biz_name",
        table_name="headless_dimension_hierarchy",
    )
    op.drop_table("headless_dimension_hierarchy")
    op.alter_column(
        "headless_dataset_asset",
        "model_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
