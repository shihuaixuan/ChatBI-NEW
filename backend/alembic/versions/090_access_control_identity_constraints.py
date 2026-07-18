"""Access Control 用户和成员关系一致性约束

Revision ID: 090_access_identity_constraints
Revises: 089_ai_model_single_default
Create Date: 2026-07-18 00:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "090_access_identity_constraints"
down_revision = "089_ai_model_single_default"
branch_labels = None
depends_on = None


def _count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar_one())


def upgrade() -> None:
    duplicate_accounts = _count(
        "SELECT COUNT(*) FROM ("
        "SELECT account FROM sys_user GROUP BY account HAVING COUNT(*) > 1"
        ") AS duplicate_account"
    )
    duplicate_memberships = _count(
        "SELECT COUNT(*) FROM ("
        "SELECT uid, oid FROM sys_user_ws GROUP BY uid, oid HAVING COUNT(*) > 1"
        ") AS duplicate_membership"
    )
    orphan_memberships = _count(
        "SELECT COUNT(*) FROM sys_user_ws AS membership "
        "LEFT JOIN sys_user AS app_user ON app_user.id = membership.uid "
        "LEFT JOIN sys_workspace AS workspace ON workspace.id = membership.oid "
        "WHERE app_user.id IS NULL OR workspace.id IS NULL"
    )
    invalid_current_workspaces = _count(
        "SELECT COUNT(*) FROM sys_user AS app_user "
        "WHERE app_user.id <> 1 AND app_user.oid <> 0 AND NOT EXISTS ("
        "SELECT 1 FROM sys_user_ws AS membership "
        "WHERE membership.uid = app_user.id AND membership.oid = app_user.oid"
        ")"
    )
    if any(
        (
            duplicate_accounts,
            duplicate_memberships,
            orphan_memberships,
            invalid_current_workspaces,
        )
    ):
        raise RuntimeError(
            "Access Control 历史数据不满足一致性要求："
            f"duplicate_accounts={duplicate_accounts}, "
            f"duplicate_memberships={duplicate_memberships}, "
            f"orphan_memberships={orphan_memberships}, "
            f"invalid_current_workspaces={invalid_current_workspaces}；"
            "请先明确修复历史数据"
        )

    op.create_unique_constraint(
        "uq_sys_user_account",
        "sys_user",
        ["account"],
    )
    op.create_unique_constraint(
        "uq_sys_user_ws_uid_oid",
        "sys_user_ws",
        ["uid", "oid"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_sys_user_ws_uid_oid",
        "sys_user_ws",
        type_="unique",
    )
    op.drop_constraint(
        "uq_sys_user_account",
        "sys_user",
        type_="unique",
    )
