"""070_headless_semantic_layer

Revision ID: 070_headless_layer
Revises: 069_semantic_asset_mgmt
Create Date: 2026-05-28 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "070_headless_layer"
down_revision = "069_semantic_asset_mgmt"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "headless_domain",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("biz_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("admin", sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True),
        sa.Column("is_open", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_headless_domain_biz_name", "headless_domain", ["oid", "biz_name"], unique=True)
    op.create_index("idx_headless_domain_status", "headless_domain", ["oid", "status"], unique=False)

    op.create_table(
        "headless_model",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("datasource_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("biz_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("model_detail", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("depends", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("filter_sql", sa.Text(), nullable=True),
        sa.Column("alias", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("source_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("ext", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_headless_model_biz_name", "headless_model", ["oid", "domain_id", "biz_name"], unique=True)
    op.create_index("idx_headless_model_status", "headless_model", ["oid", "domain_id", "status"], unique=False)

    op.create_table(
        "headless_model_relation",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("left_model_id", sa.BigInteger(), nullable=False),
        sa.Column("right_model_id", sa.BigInteger(), nullable=False),
        sa.Column("join_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("join_conditions", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("ext", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_headless_model_relation_domain", "headless_model_relation", ["oid", "domain_id", "status"], unique=False)
    op.create_index("idx_headless_model_relation_models", "headless_model_relation", ["oid", "left_model_id", "right_model_id", "status"], unique=False)

    op.create_table(
        "headless_metric",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("biz_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("sensitive_level", sa.Integer(), nullable=False),
        sa.Column("type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("default_agg", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column("data_format_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("data_format", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("alias", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("classifications", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("relate_dimensions", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("type_params", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("ext", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("define_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("is_publish", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_tag", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_headless_metric_biz_name", "headless_metric", ["oid", "model_id", "biz_name"], unique=True)
    op.create_index("idx_headless_metric_status", "headless_metric", ["oid", "model_id", "status"], unique=False)

    op.create_table(
        "headless_dimension",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("biz_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("sensitive_level", sa.Integer(), nullable=False),
        sa.Column("type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("semantic_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("alias", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("default_values", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("dim_value_maps", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("type_params", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("expr", sa.Text(), nullable=True),
        sa.Column("data_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("is_tag", sa.Integer(), nullable=False),
        sa.Column("ext", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_headless_dimension_biz_name", "headless_dimension", ["oid", "model_id", "biz_name"], unique=True)
    op.create_index("idx_headless_dimension_status", "headless_dimension", ["oid", "model_id", "status"], unique=False)

    op.create_table(
        "headless_dataset",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("biz_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("alias", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("data_set_detail", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{\"dataSetModelConfigs\": []}'::jsonb"), nullable=False),
        sa.Column("query_config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_headless_dataset_biz_name", "headless_dataset", ["oid", "domain_id", "biz_name"], unique=True)
    op.create_index("idx_headless_dataset_status", "headless_dataset", ["oid", "domain_id", "status"], unique=False)

    op.create_table(
        "headless_term",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("alias", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("related_metrics", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("related_dimensions", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_headless_term_domain", "headless_term", ["oid", "domain_id", "status"], unique=False)

    op.create_table(
        "headless_schema_index",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("element_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("element_id", sa.BigInteger(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_headless_schema_index_element", "headless_schema_index", ["oid", "dataset_id", "element_type", "element_id"], unique=True)
    op.create_index("idx_headless_schema_index_text", "headless_schema_index", ["oid", "dataset_id", "element_type"], unique=False)


def downgrade():
    op.drop_index("idx_headless_schema_index_text", table_name="headless_schema_index")
    op.drop_index("ux_headless_schema_index_element", table_name="headless_schema_index")
    op.drop_table("headless_schema_index")

    op.drop_index("idx_headless_term_domain", table_name="headless_term")
    op.drop_table("headless_term")

    op.drop_index("idx_headless_dataset_status", table_name="headless_dataset")
    op.drop_index("ux_headless_dataset_biz_name", table_name="headless_dataset")
    op.drop_table("headless_dataset")

    op.drop_index("idx_headless_dimension_status", table_name="headless_dimension")
    op.drop_index("ux_headless_dimension_biz_name", table_name="headless_dimension")
    op.drop_table("headless_dimension")

    op.drop_index("idx_headless_metric_status", table_name="headless_metric")
    op.drop_index("ux_headless_metric_biz_name", table_name="headless_metric")
    op.drop_table("headless_metric")

    op.drop_index("idx_headless_model_relation_models", table_name="headless_model_relation")
    op.drop_index("idx_headless_model_relation_domain", table_name="headless_model_relation")
    op.drop_table("headless_model_relation")

    op.drop_index("idx_headless_model_status", table_name="headless_model")
    op.drop_index("ux_headless_model_biz_name", table_name="headless_model")
    op.drop_table("headless_model")

    op.drop_index("idx_headless_domain_status", table_name="headless_domain")
    op.drop_index("ux_headless_domain_biz_name", table_name="headless_domain")
    op.drop_table("headless_domain")
