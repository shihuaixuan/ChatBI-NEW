"""data_training 升级为带生命周期的 verified query 资产（P0-5）。

status 复用既有 verification_status 列（扩展 DEPRECATED 取值），
新增 source / verified_by / verified_at / semantic_plan / plan_fingerprint /
use_as_onboarding 列，全部只增不破坏。
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "110_verified_query_upgrade"
down_revision = "109_chatbi_memory_recall_variant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 生命周期新增 DEPRECATED，必须同步扩展 094 创建的数据库检查约束。
    op.drop_constraint(
        "ck_data_training_verification_status",
        "data_training",
        type_="check",
    )
    op.create_check_constraint(
        "ck_data_training_verification_status",
        "data_training",
        "verification_status IN ('UNVERIFIED', 'VERIFIED', 'DEPRECATED')",
    )
    op.add_column(
        "data_training",
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
    )
    op.add_column("data_training", sa.Column("verified_by", sa.BigInteger(), nullable=True))
    op.add_column(
        "data_training",
        sa.Column("verified_at", sa.DateTime(timezone=False), nullable=True),
    )
    op.add_column("data_training", sa.Column("semantic_plan", JSONB(), nullable=True))
    op.add_column(
        "data_training",
        sa.Column("plan_fingerprint", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "data_training",
        sa.Column(
            "use_as_onboarding",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    # 旧版本不能识别 DEPRECATED，回退前统一恢复为未认证状态。
    op.execute(
        "UPDATE data_training "
        "SET verification_status = 'UNVERIFIED' "
        "WHERE verification_status = 'DEPRECATED'"
    )
    op.drop_constraint(
        "ck_data_training_verification_status",
        "data_training",
        type_="check",
    )
    op.create_check_constraint(
        "ck_data_training_verification_status",
        "data_training",
        "verification_status IN ('UNVERIFIED', 'VERIFIED')",
    )
    op.drop_column("data_training", "use_as_onboarding")
    op.drop_column("data_training", "plan_fingerprint")
    op.drop_column("data_training", "semantic_plan")
    op.drop_column("data_training", "verified_at")
    op.drop_column("data_training", "verified_by")
    op.drop_column("data_training", "source")
