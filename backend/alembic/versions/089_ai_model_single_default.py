"""约束 AI Model 只能有一个默认模型

Revision ID: 089_ai_model_single_default
Revises: 088_remove_legacy_terminology
Create Date: 2026-07-18 00:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "089_ai_model_single_default"
down_revision = "088_remove_legacy_terminology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 已有模型必须恰好有一个默认项，禁止迁移过程擅自选择或清除默认模型。
    model_count, default_count = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) AS model_count, "
            "COUNT(*) FILTER (WHERE default_model = true) AS default_count "
            "FROM ai_model"
        )
    ).one()
    if model_count and default_count != 1:
        raise RuntimeError(
            "ai_model 默认模型数据不满足唯一约束："
            f"model_count={model_count}, default_count={default_count}；"
            "请先明确设置一个默认模型"
        )

    op.create_index(
        "ux_ai_model_single_default",
        "ai_model",
        ["default_model"],
        unique=True,
        postgresql_where=sa.text("default_model = true"),
    )


def downgrade() -> None:
    op.drop_index("ux_ai_model_single_default", table_name="ai_model")
