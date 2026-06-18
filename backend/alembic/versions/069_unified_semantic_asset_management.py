"""069_unified_semantic_asset_management

Revision ID: 069_semantic_asset_mgmt
Revises: 068_agentic_chat_flow
Create Date: 2026-05-28 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "069_semantic_asset_mgmt"
down_revision = "068_agentic_chat_flow"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "semantic_dataset",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("datasource_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("business_domain", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column("default_time_dimension_id", sa.BigInteger(), nullable=True),
        sa.Column("default_metric_ids", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("default_filter", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="CANDIDATE", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_semantic_dataset_name", "semantic_dataset", ["oid", "datasource_id", "name"], unique=True)
    op.create_index("idx_semantic_dataset_status", "semantic_dataset", ["oid", "datasource_id", "status"], unique=False)

    op.create_table(
        "semantic_model",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("source_type", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="TABLE", nullable=False),
        sa.Column("table_ids", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("base_sql", sa.Text(), nullable=True),
        sa.Column("primary_key_dimension_id", sa.BigInteger(), nullable=True),
        sa.Column("default_time_dimension_id", sa.BigInteger(), nullable=True),
        sa.Column("filter_sql", sa.Text(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="CANDIDATE", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_semantic_model_name", "semantic_model", ["dataset_id", "name"], unique=True)
    op.create_index("idx_semantic_model_status", "semantic_model", ["dataset_id", "status"], unique=False)

    op.create_table(
        "semantic_asset_relation",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("datasource_id", sa.BigInteger(), nullable=True),
        sa.Column("dataset_id", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column("source_asset_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("source_asset_id", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("target_asset_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("target_asset_id", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("relation_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("weight", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="APPROVED", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_semantic_asset_relation",
        "semantic_asset_relation",
        ["oid", "dataset_id", "source_asset_type", "source_asset_id", "target_asset_type", "target_asset_id", "relation_type"],
        unique=True,
    )
    op.create_index("idx_semantic_asset_relation_source", "semantic_asset_relation", ["source_asset_type", "source_asset_id"], unique=False)
    op.create_index("idx_semantic_asset_relation_target", "semantic_asset_relation", ["target_asset_type", "target_asset_id"], unique=False)

    op.create_table(
        "semantic_asset_retrieval_document",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("asset_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("asset_id", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("doc_id", sqlmodel.sql.sqltypes.AutoString(length=192), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(length=256), nullable=False),
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("business_text", sa.Text(), nullable=True),
        sa.Column("technical_text", sa.Text(), nullable=True),
        sa.Column("related_terms", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("related_examples", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("relations", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("version", sqlmodel.sql.sqltypes.AutoString(length=128), server_default="dynamic", nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_semantic_asset_retrieval_document",
        "semantic_asset_retrieval_document",
        ["oid", "dataset_id", "asset_type", "asset_id"],
        unique=True,
    )
    op.create_index(
        "idx_semantic_asset_retrieval_document_doc_id",
        "semantic_asset_retrieval_document",
        ["doc_id"],
        unique=False,
    )

    op.create_table(
        "semantic_asset_index_version",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("index_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("version", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="READY", nullable=False),
        sa.Column("last_built_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_semantic_asset_index_version", "semantic_asset_index_version", ["oid", "dataset_id", "index_type"], unique=True)

    op.create_table(
        "semantic_asset_quality_score",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("asset_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("asset_id", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("dataset_id", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column("score", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("issues", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_semantic_asset_quality", "semantic_asset_quality_score", ["oid", "asset_type", "asset_id", "dataset_id"], unique=True)

    op.add_column("semantic_metric", sa.Column("dataset_ids", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("semantic_metric", sa.Column("metric_group", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True))
    op.add_column("semantic_metric", sa.Column("is_core", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("semantic_metric", sa.Column("related_term_ids", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("semantic_metric", sa.Column("related_example_ids", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))

    op.add_column("semantic_dimension", sa.Column("dataset_ids", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("semantic_dimension", sa.Column("is_primary_key", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("semantic_dimension", sa.Column("is_default_time", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("semantic_dimension", sa.Column("is_default_group_by", sa.Boolean(), server_default=sa.text("false"), nullable=False))

    op.add_column("semantic_dimension_value", sa.Column("dataset_ids", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))

    op.add_column("terminology", sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("terminology", sa.Column("dataset_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("terminology", sa.Column("mapped_assets", postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    op.add_column("data_training", sa.Column("example_type", sqlmodel.sql.sqltypes.AutoString(length=32), server_default="QUESTION_EXAMPLE", nullable=True))
    op.add_column("data_training", sa.Column("sql", sa.Text(), nullable=True))
    op.add_column("data_training", sa.Column("linked_assets", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("data_training", sa.Column("dataset_id", sa.BigInteger(), nullable=True))


def downgrade():
    op.drop_column("data_training", "dataset_id")
    op.drop_column("data_training", "linked_assets")
    op.drop_column("data_training", "sql")
    op.drop_column("data_training", "example_type")

    op.drop_column("terminology", "mapped_assets")
    op.drop_column("terminology", "dataset_ids")
    op.drop_column("terminology", "aliases")

    op.drop_column("semantic_dimension_value", "dataset_ids")

    op.drop_column("semantic_dimension", "is_default_group_by")
    op.drop_column("semantic_dimension", "is_default_time")
    op.drop_column("semantic_dimension", "is_primary_key")
    op.drop_column("semantic_dimension", "dataset_ids")

    op.drop_column("semantic_metric", "related_example_ids")
    op.drop_column("semantic_metric", "related_term_ids")
    op.drop_column("semantic_metric", "is_core")
    op.drop_column("semantic_metric", "metric_group")
    op.drop_column("semantic_metric", "dataset_ids")

    op.drop_index("ux_semantic_asset_quality", table_name="semantic_asset_quality_score")
    op.drop_table("semantic_asset_quality_score")

    op.drop_index("ux_semantic_asset_index_version", table_name="semantic_asset_index_version")
    op.drop_table("semantic_asset_index_version")

    op.drop_index("idx_semantic_asset_retrieval_document_doc_id", table_name="semantic_asset_retrieval_document")
    op.drop_index("ux_semantic_asset_retrieval_document", table_name="semantic_asset_retrieval_document")
    op.drop_table("semantic_asset_retrieval_document")

    op.drop_index("idx_semantic_asset_relation_target", table_name="semantic_asset_relation")
    op.drop_index("idx_semantic_asset_relation_source", table_name="semantic_asset_relation")
    op.drop_index("ux_semantic_asset_relation", table_name="semantic_asset_relation")
    op.drop_table("semantic_asset_relation")

    op.drop_index("idx_semantic_model_status", table_name="semantic_model")
    op.drop_index("ux_semantic_model_name", table_name="semantic_model")
    op.drop_table("semantic_model")

    op.drop_index("idx_semantic_dataset_status", table_name="semantic_dataset")
    op.drop_index("ux_semantic_dataset_name", table_name="semantic_dataset")
    op.drop_table("semantic_dataset")
