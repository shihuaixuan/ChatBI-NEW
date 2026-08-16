"""新增数据集级模块化 instructions 资产。"""

import sqlalchemy as sa

from alembic import op

revision = "114_dataset_instructions"
down_revision = "113_agent_run_execution_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "headless_dataset_instruction",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("oid", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("module", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=False), nullable=True),
        sa.CheckConstraint(
            "module IN ('sql_generation', 'question_categorization')",
            name="ck_headless_dataset_instruction_module",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "oid",
            "dataset_id",
            "module",
            "version",
            name="ux_headless_dataset_instruction_version",
        ),
    )
    op.create_index(
        "idx_headless_dataset_instruction_enabled",
        "headless_dataset_instruction",
        ["oid", "dataset_id", "module", "enabled"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_headless_dataset_instruction_enabled",
        table_name="headless_dataset_instruction",
    )
    op.drop_table("headless_dataset_instruction")
