"""新增 ChatBI 用户长期记忆和记忆证据表。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "104_chatbi_memory"
down_revision = "103_agent_run_cancellation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chatbi_memory",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("layer", sa.String(length=16), nullable=False, server_default=sa.text("'atom'")),
        sa.Column("memory_type", sa.String(length=64), nullable=False),
        sa.Column("memory_key", sa.String(length=160), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False, server_default=sa.text("0.5")),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'candidate'")),
        sa.Column("last_confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_chatbi_memory_user_status",
        "chatbi_memory",
        ["oid", "user_id", "status", "layer", "updated_at"],
    )
    op.create_index(
        "idx_chatbi_memory_user_key",
        "chatbi_memory",
        ["oid", "user_id", "memory_type", "memory_key"],
    )

    op.create_table(
        "chatbi_memory_evidence",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("memory_id", sa.BigInteger(), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("evidence_type", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=160), nullable=True),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("strength", sa.Numeric(5, 4), nullable=False, server_default=sa.text("0.5")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["chatbi_memory.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_chatbi_memory_evidence_memory",
        "chatbi_memory_evidence",
        ["oid", "user_id", "memory_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_chatbi_memory_evidence_memory",
        table_name="chatbi_memory_evidence",
    )
    op.drop_table("chatbi_memory_evidence")
    op.drop_index("idx_chatbi_memory_user_key", table_name="chatbi_memory")
    op.drop_index("idx_chatbi_memory_user_status", table_name="chatbi_memory")
    op.drop_table("chatbi_memory")
