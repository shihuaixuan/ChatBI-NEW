"""新增 ChatBI 用户记忆独立向量索引表。"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR


revision = "106_chatbi_memory_embedding"
down_revision = "105_chatbi_memory_behavior"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chatbi_memory_embedding",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("memory_id", sa.BigInteger(), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("embedding_profile", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False, server_default=sa.text("1024")),
        sa.Column("embedding", VECTOR(1024), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["chatbi_memory.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("dimension = 1024", name="ck_chatbi_memory_embedding_dimension"),
        sa.CheckConstraint(
            "status <> 'active' OR embedding IS NOT NULL",
            name="ck_chatbi_memory_embedding_active_vector",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "memory_id",
            "embedding_profile",
            name="ux_chatbi_memory_embedding_profile",
        ),
    )
    op.create_index(
        "idx_chatbi_memory_embedding_lookup",
        "chatbi_memory_embedding",
        ["oid", "user_id", "embedding_profile", "status"],
    )
    op.create_index(
        "idx_chatbi_memory_embedding_hnsw",
        "chatbi_memory_embedding",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("status = 'active' AND embedding IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_chatbi_memory_embedding_hnsw",
        table_name="chatbi_memory_embedding",
    )
    op.drop_index(
        "idx_chatbi_memory_embedding_lookup",
        table_name="chatbi_memory_embedding",
    )
    op.drop_table("chatbi_memory_embedding")
