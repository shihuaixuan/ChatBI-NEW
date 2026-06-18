"""071_add_headless_model_relation_guard

Revision ID: 071_headless_relation_guard
Revises: 070_headless_layer
Create Date: 2026-05-29 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "071_headless_relation_guard"
down_revision = "070_headless_layer"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return inspector.has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade():
    if not _has_table("headless_model_relation"):
        op.create_table(
            "headless_model_relation",
            sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
            sa.Column("oid", sa.BigInteger(), nullable=False),
            sa.Column("domain_id", sa.BigInteger(), nullable=False),
            sa.Column("left_model_id", sa.BigInteger(), nullable=False),
            sa.Column("right_model_id", sa.BigInteger(), nullable=False),
            sa.Column("join_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
            sa.Column(
                "join_conditions",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'[]'::jsonb"),
                nullable=False,
            ),
            sa.Column("status", sa.Integer(), nullable=False),
            sa.Column("ext", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_index("headless_model_relation", "idx_headless_model_relation_domain"):
        op.create_index(
            "idx_headless_model_relation_domain",
            "headless_model_relation",
            ["oid", "domain_id", "status"],
            unique=False,
        )
    if not _has_index("headless_model_relation", "idx_headless_model_relation_models"):
        op.create_index(
            "idx_headless_model_relation_models",
            "headless_model_relation",
            ["oid", "left_model_id", "right_model_id", "status"],
            unique=False,
        )


def downgrade():
    if _has_table("headless_model_relation"):
        if _has_index("headless_model_relation", "idx_headless_model_relation_models"):
            op.drop_index("idx_headless_model_relation_models", table_name="headless_model_relation")
        if _has_index("headless_model_relation", "idx_headless_model_relation_domain"):
            op.drop_index("idx_headless_model_relation_domain", table_name="headless_model_relation")
        op.drop_table("headless_model_relation")
