"""072_headless_asset_storage_p0

Revision ID: 072_headless_asset_p0
Revises: 071_headless_relation_guard
Create Date: 2026-06-03 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "072_headless_asset_p0"
down_revision = "071_headless_relation_guard"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("headless_domain", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("headless_domain", sa.Column("owner", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True))
    op.add_column("headless_domain", sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True))
    op.add_column("headless_domain", sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True))

    op.add_column("headless_model", sa.Column("database_name", sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True))
    op.add_column("headless_model", sa.Column("schema_name", sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True))
    op.add_column("headless_model", sa.Column("table_name", sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True))
    op.add_column("headless_model", sa.Column("sql_query", sa.Text(), nullable=True))
    op.add_column(
        "headless_model",
        sa.Column("primary_key", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column(
        "headless_model",
        sa.Column("model_grain", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column("headless_model", sa.Column("default_time_field", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True))
    op.add_column("headless_model", sa.Column("is_view", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("headless_model", sa.Column("schema_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False))
    op.add_column("headless_model", sa.Column("last_schema_sync_at", sa.DateTime(), nullable=True))

    op.add_column("headless_metric", sa.Column("measure_id", sa.BigInteger(), nullable=True))
    op.add_column("headless_metric", sa.Column("field_id", sa.BigInteger(), nullable=True))
    op.add_column("headless_metric", sa.Column("expr", sa.Text(), nullable=True))
    op.add_column("headless_metric", sa.Column("filter_sql", sa.Text(), nullable=True))
    op.add_column(
        "headless_metric",
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column(
        "headless_metric",
        sa.Column("metric_refs", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column("headless_metric", sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False))
    op.add_column("headless_metric", sa.Column("quality_status", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True))
    op.add_column("headless_metric", sa.Column("quality_message", sa.Text(), nullable=True))

    op.add_column("headless_dimension", sa.Column("field_id", sa.BigInteger(), nullable=True))
    op.add_column("headless_dimension", sa.Column("field_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True))
    op.add_column("headless_dimension", sa.Column("is_primary_key", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("headless_dimension", sa.Column("is_default_time", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column(
        "headless_dimension",
        sa.Column("time_granularities", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column("headless_dimension", sa.Column("value_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True))
    op.add_column("headless_dimension", sa.Column("value_source_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True))
    op.add_column("headless_dimension", sa.Column("value_query_sql", sa.Text(), nullable=True))

    op.add_column("headless_dataset", sa.Column("schema_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False))
    op.add_column("headless_dataset", sa.Column("index_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False))
    op.add_column("headless_dataset", sa.Column("default_model_id", sa.BigInteger(), nullable=True))
    op.add_column("headless_dataset", sa.Column("default_time_dimension_id", sa.BigInteger(), nullable=True))
    op.add_column("headless_dataset", sa.Column("owner", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True))

    op.create_table(
        "headless_model_field",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=False),
        sa.Column("field_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("biz_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("expr", sa.Text(), nullable=False),
        sa.Column("data_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("field_role", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("semantic_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("alias", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("type_params", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("source_order", sa.Integer(), nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_headless_model_field_biz_name", "headless_model_field", ["oid", "model_id", "biz_name"], unique=True)
    op.create_index("idx_headless_model_field_role", "headless_model_field", ["oid", "model_id", "field_role", "status"])

    op.create_table(
        "headless_model_measure",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=False),
        sa.Column("field_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("biz_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("expr", sa.Text(), nullable=False),
        sa.Column("agg", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column("data_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("alias", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type_params", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_headless_model_measure_biz_name", "headless_model_measure", ["oid", "model_id", "biz_name"], unique=True)
    op.create_index("idx_headless_model_measure_status", "headless_model_measure", ["oid", "model_id", "status"])

    op.create_table(
        "headless_dimension_value",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("dimension_id", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("display_value", sa.Text(), nullable=True),
        sa.Column("biz_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column("alias", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("frequency", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_headless_dimension_value_dimension", "headless_dimension_value", ["oid", "dimension_id", "status"])
    op.create_index("idx_headless_dimension_value_value", "headless_dimension_value", ["oid", "dimension_id", "value"])

    op.create_table(
        "headless_dataset_model_config",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=False),
        sa.Column("includes_all", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_headless_dataset_model", "headless_dataset_model_config", ["oid", "dataset_id", "model_id"], unique=True)

    op.create_table(
        "headless_dataset_asset",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=False),
        sa.Column("asset_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_headless_dataset_asset", "headless_dataset_asset", ["oid", "dataset_id", "asset_type", "asset_id"], unique=True)
    op.create_index("idx_headless_dataset_asset_model", "headless_dataset_asset", ["oid", "dataset_id", "model_id", "asset_type", "status"])


def downgrade():
    op.drop_index("idx_headless_dataset_asset_model", table_name="headless_dataset_asset")
    op.drop_index("ux_headless_dataset_asset", table_name="headless_dataset_asset")
    op.drop_table("headless_dataset_asset")

    op.drop_index("ux_headless_dataset_model", table_name="headless_dataset_model_config")
    op.drop_table("headless_dataset_model_config")

    op.drop_index("idx_headless_dimension_value_value", table_name="headless_dimension_value")
    op.drop_index("idx_headless_dimension_value_dimension", table_name="headless_dimension_value")
    op.drop_table("headless_dimension_value")

    op.drop_index("idx_headless_model_measure_status", table_name="headless_model_measure")
    op.drop_index("ux_headless_model_measure_biz_name", table_name="headless_model_measure")
    op.drop_table("headless_model_measure")

    op.drop_index("idx_headless_model_field_role", table_name="headless_model_field")
    op.drop_index("ux_headless_model_field_biz_name", table_name="headless_model_field")
    op.drop_table("headless_model_field")

    for column in ["owner", "default_time_dimension_id", "default_model_id", "index_version", "schema_version"]:
        op.drop_column("headless_dataset", column)

    for column in [
        "value_query_sql",
        "value_source_type",
        "value_type",
        "time_granularities",
        "is_default_time",
        "is_primary_key",
        "field_name",
        "field_id",
    ]:
        op.drop_column("headless_dimension", column)

    for column in [
        "quality_message",
        "quality_status",
        "version",
        "metric_refs",
        "fields",
        "filter_sql",
        "expr",
        "field_id",
        "measure_id",
    ]:
        op.drop_column("headless_metric", column)

    for column in [
        "last_schema_sync_at",
        "schema_version",
        "is_view",
        "default_time_field",
        "model_grain",
        "primary_key",
        "sql_query",
        "table_name",
        "schema_name",
        "database_name",
    ]:
        op.drop_column("headless_model", column)

    for column in ["updated_by", "created_by", "owner", "description"]:
        op.drop_column("headless_domain", column)
