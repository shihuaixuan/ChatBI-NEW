"""Access Control API Key 一致性约束

Revision ID: 091_access_api_key_constraints
Revises: 090_access_identity_constraints
Create Date: 2026-07-18 00:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "091_access_api_key_constraints"
down_revision = "090_access_identity_constraints"
branch_labels = None
depends_on = None


def _count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar_one())


def upgrade() -> None:
    duplicate_access_keys = _count(
        "SELECT COUNT(*) FROM ("
        "SELECT access_key FROM sys_apikey "
        "GROUP BY access_key HAVING COUNT(*) > 1"
        ") AS duplicate_api_key"
    )
    orphan_api_keys = _count(
        "SELECT COUNT(*) FROM sys_apikey AS api_key "
        "LEFT JOIN sys_user AS app_user ON app_user.id = api_key.uid "
        "WHERE app_user.id IS NULL"
    )
    users_over_limit = _count(
        "SELECT COUNT(*) FROM ("
        "SELECT uid FROM sys_apikey GROUP BY uid HAVING COUNT(*) > 5"
        ") AS over_limit"
    )
    if any((duplicate_access_keys, orphan_api_keys, users_over_limit)):
        raise RuntimeError(
            "Access Control API Key 历史数据不满足一致性要求："
            f"duplicate_access_keys={duplicate_access_keys}, "
            f"orphan_api_keys={orphan_api_keys}, "
            f"users_over_limit={users_over_limit}；"
            "请先明确修复历史数据"
        )

    op.create_unique_constraint(
        "uq_sys_apikey_access_key",
        "sys_apikey",
        ["access_key"],
    )
    op.create_index("ix_sys_apikey_uid", "sys_apikey", ["uid"])


def downgrade() -> None:
    op.drop_index("ix_sys_apikey_uid", table_name="sys_apikey")
    op.drop_constraint(
        "uq_sys_apikey_access_key",
        "sys_apikey",
        type_="unique",
    )
