"""修复已标记 101 但缺少语义契约表的数据库。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "102_repair_semantic_tables"
down_revision = "101_semantic_contract_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("headless_business_entity"):
        op.create_table(
            "headless_business_entity",
            sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
            sa.Column("oid", sa.BigInteger(), nullable=False),
            sa.Column("domain_id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("biz_name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("key_type", sa.String(length=64), nullable=False),
            sa.Column("value_domain_key", sa.String(length=128), nullable=True),
            sa.Column("status", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ux_headless_business_entity_biz_name",
            "headless_business_entity",
            ["oid", "domain_id", "biz_name"],
            unique=True,
        )
        op.create_index(
            "idx_headless_business_entity_status",
            "headless_business_entity",
            ["oid", "domain_id", "status"],
        )

    if not inspector.has_table("headless_logical_dimension"):
        op.create_table(
            "headless_logical_dimension",
            sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
            sa.Column("oid", sa.BigInteger(), nullable=False),
            sa.Column("domain_id", sa.BigInteger(), nullable=False),
            sa.Column("entity_id", sa.BigInteger(), nullable=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("biz_name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("semantic_type", sa.String(length=64), nullable=False),
            sa.Column("value_type", sa.String(length=64), nullable=False),
            sa.Column("value_domain_key", sa.String(length=128), nullable=True),
            sa.Column("status", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ux_headless_logical_dimension_biz_name",
            "headless_logical_dimension",
            ["oid", "domain_id", "biz_name"],
            unique=True,
        )
        op.create_index(
            "idx_headless_logical_dimension_status",
            "headless_logical_dimension",
            ["oid", "domain_id", "status"],
        )

    if not inspector.has_table("headless_metric_dimension_capability"):
        op.create_table(
            "headless_metric_dimension_capability",
            sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
            sa.Column("oid", sa.BigInteger(), nullable=False),
            sa.Column("metric_id", sa.BigInteger(), nullable=False),
            sa.Column("logical_dimension_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "usages",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("binding_strategy", sa.String(length=32), nullable=False),
            sa.Column(
                "relation_path",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("target_model_id", sa.BigInteger(), nullable=False),
            sa.Column("physical_dimension_id", sa.BigInteger(), nullable=True),
            sa.Column("aggregation_safety", sa.String(length=32), nullable=False),
            sa.Column(
                "pre_aggregation_grain",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "time_alignment_policy",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'NONE'"),
            ),
            sa.Column("status", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
            sa.Column(
                "ext",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ux_headless_metric_dimension_capability",
            "headless_metric_dimension_capability",
            ["oid", "metric_id", "logical_dimension_id"],
            unique=True,
        )
        op.create_index(
            "idx_headless_metric_dimension_capability_status",
            "headless_metric_dimension_capability",
            ["oid", "metric_id", "status"],
        )


def downgrade() -> None:
    # 这些表属于 101 的目标结构，回退修复迁移时不应删除。
    pass
