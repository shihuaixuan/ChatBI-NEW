"""Assistant 应用标识、类型和工作空间约束

Revision ID: 092_assistant_constraints
Revises: 091_access_api_key_constraints
Create Date: 2026-07-18 00:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "092_assistant_constraints"
down_revision = "091_access_api_key_constraints"
branch_labels = None
depends_on = None


def _count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar_one())


def upgrade() -> None:
    duplicate_app_ids = _count(
        "SELECT COUNT(*) FROM ("
        "SELECT app_id FROM sys_assistant WHERE app_id IS NOT NULL "
        "GROUP BY app_id HAVING COUNT(*) > 1"
        ") AS duplicate_app_id"
    )
    duplicate_app_secrets = _count(
        "SELECT COUNT(*) FROM ("
        "SELECT app_secret FROM sys_assistant WHERE app_secret IS NOT NULL "
        "GROUP BY app_secret HAVING COUNT(*) > 1"
        ") AS duplicate_app_secret"
    )
    missing_workspaces = _count(
        "SELECT COUNT(*) FROM sys_assistant WHERE oid IS NULL"
    )
    invalid_types = _count(
        "SELECT COUNT(*) FROM sys_assistant WHERE type NOT IN (0, 1, 2, 3, 4)"
    )
    if any(
        (
            duplicate_app_ids,
            duplicate_app_secrets,
            missing_workspaces,
            invalid_types,
        )
    ):
        raise RuntimeError(
            "Assistant 历史数据不满足一致性要求："
            f"duplicate_app_ids={duplicate_app_ids}, "
            f"duplicate_app_secrets={duplicate_app_secrets}, "
            f"missing_workspaces={missing_workspaces}, "
            f"invalid_types={invalid_types}；"
            "请先明确修复历史数据"
        )

    op.alter_column(
        "sys_assistant",
        "oid",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_sys_assistant_type",
        "sys_assistant",
        "type IN (0, 1, 2, 3, 4)",
    )
    op.create_unique_constraint(
        "uq_sys_assistant_app_id",
        "sys_assistant",
        ["app_id"],
    )
    op.create_unique_constraint(
        "uq_sys_assistant_app_secret",
        "sys_assistant",
        ["app_secret"],
    )
    op.create_index(
        "ix_sys_assistant_oid_type",
        "sys_assistant",
        ["oid", "type"],
    )


def downgrade() -> None:
    op.drop_index("ix_sys_assistant_oid_type", table_name="sys_assistant")
    op.drop_constraint(
        "uq_sys_assistant_app_secret",
        "sys_assistant",
        type_="unique",
    )
    op.drop_constraint(
        "uq_sys_assistant_app_id",
        "sys_assistant",
        type_="unique",
    )
    op.drop_constraint(
        "ck_sys_assistant_type",
        "sys_assistant",
        type_="check",
    )
    op.alter_column(
        "sys_assistant",
        "oid",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
