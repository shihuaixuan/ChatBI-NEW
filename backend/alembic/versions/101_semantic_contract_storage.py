"""新增完整语义契约的基础存储。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "101_semantic_contract_storage"
down_revision = "100_chatbi_agent_trace_node"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 历史资产字段先保持可空，发布校验在后续批次统一收紧。
    op.add_column("headless_model", sa.Column("model_kind", sa.String(length=32), nullable=True))
    op.add_column("headless_model", sa.Column("row_description", sa.Text(), nullable=True))
    op.add_column("headless_model", sa.Column("event_time_field", sa.String(length=128), nullable=True))
    op.add_column("headless_model", sa.Column("snapshot_time_field", sa.String(length=128), nullable=True))
    op.add_column("headless_model", sa.Column("contract_status", sa.String(length=32), nullable=True))
    op.add_column("headless_model", sa.Column("contract_version", sa.BigInteger(), nullable=True))
    op.create_index(
        "idx_headless_model_contract",
        "headless_model",
        ["oid", "contract_status", "contract_version"],
    )

    op.add_column(
        "headless_metric",
        sa.Column(
            "result_grain",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("headless_metric", sa.Column("additivity", sa.String(length=32), nullable=True))
    op.add_column("headless_metric", sa.Column("distinct_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("headless_metric", sa.Column("time_semantics", sa.String(length=32), nullable=True))
    op.add_column("headless_metric", sa.Column("default_time_dimension_id", sa.BigInteger(), nullable=True))
    op.add_column("headless_metric", sa.Column("snapshot_aggregation", sa.String(length=32), nullable=True))
    op.add_column("headless_metric", sa.Column("contract_version", sa.BigInteger(), nullable=True))
    op.create_index(
        "idx_headless_metric_contract",
        "headless_metric",
        ["oid", "contract_version", "quality_status"],
    )

    op.add_column("headless_dimension", sa.Column("logical_dimension_id", sa.BigInteger(), nullable=True))
    op.add_column("headless_dimension", sa.Column("binding_role", sa.String(length=32), nullable=True))
    op.add_column("headless_dimension", sa.Column("binding_priority", sa.Integer(), nullable=True))
    op.add_column("headless_dimension", sa.Column("contract_version", sa.BigInteger(), nullable=True))
    op.create_index(
        "idx_headless_dimension_logical",
        "headless_dimension",
        ["oid", "model_id", "logical_dimension_id", "status"],
    )

    op.add_column("headless_model_relation", sa.Column("cardinality", sa.String(length=16), nullable=True))
    op.add_column("headless_model_relation", sa.Column("left_unique", sa.Boolean(), nullable=True))
    op.add_column("headless_model_relation", sa.Column("right_unique", sa.Boolean(), nullable=True))
    op.add_column("headless_model_relation", sa.Column("metric_propagation", sa.String(length=32), nullable=True))
    op.add_column("headless_model_relation", sa.Column("aggregation_safety", sa.String(length=32), nullable=True))
    op.add_column("headless_model_relation", sa.Column("valid_time_condition", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("headless_model_relation", sa.Column("contract_status", sa.String(length=32), nullable=True))
    op.add_column("headless_model_relation", sa.Column("contract_version", sa.BigInteger(), nullable=True))
    op.create_index(
        "idx_headless_model_relation_contract",
        "headless_model_relation",
        ["oid", "contract_status", "contract_version"],
    )

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

    op.create_table(
        "headless_metric_dimension_capability",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("metric_id", sa.BigInteger(), nullable=False),
        sa.Column("logical_dimension_id", sa.BigInteger(), nullable=False),
        sa.Column("usages", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("binding_strategy", sa.String(length=32), nullable=False),
        sa.Column("relation_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("target_model_id", sa.BigInteger(), nullable=False),
        sa.Column("physical_dimension_id", sa.BigInteger(), nullable=True),
        sa.Column("aggregation_safety", sa.String(length=32), nullable=False),
        sa.Column("pre_aggregation_grain", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("time_alignment_policy", sa.String(length=32), nullable=False, server_default=sa.text("'NONE'")),
        sa.Column("status", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("ext", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
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
    op.drop_table("headless_metric_dimension_capability")
    op.drop_table("headless_logical_dimension")
    op.drop_table("headless_business_entity")

    op.drop_index("idx_headless_model_relation_contract", table_name="headless_model_relation")
    for column in (
        "contract_version",
        "contract_status",
        "valid_time_condition",
        "aggregation_safety",
        "metric_propagation",
        "right_unique",
        "left_unique",
        "cardinality",
    ):
        op.drop_column("headless_model_relation", column)

    op.drop_index("idx_headless_dimension_logical", table_name="headless_dimension")
    for column in ("contract_version", "binding_priority", "binding_role", "logical_dimension_id"):
        op.drop_column("headless_dimension", column)

    op.drop_index("idx_headless_metric_contract", table_name="headless_metric")
    for column in (
        "contract_version",
        "snapshot_aggregation",
        "default_time_dimension_id",
        "time_semantics",
        "distinct_keys",
        "additivity",
        "result_grain",
    ):
        op.drop_column("headless_metric", column)

    op.drop_index("idx_headless_model_contract", table_name="headless_model")
    for column in (
        "contract_version",
        "contract_status",
        "snapshot_time_field",
        "event_time_field",
        "row_description",
        "model_kind",
    ):
        op.drop_column("headless_model", column)
