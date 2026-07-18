"""权限变量定义约束

Revision ID: 093_access_policy_constraints
Revises: 092_assistant_constraints
Create Date: 2026-07-18 00:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "093_access_policy_constraints"
down_revision = "092_assistant_constraints"
branch_labels = None
depends_on = None


def _count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar_one())


def upgrade() -> None:
    duplicate_names = _count(
        "SELECT COUNT(*) FROM ("
        "SELECT name FROM system_variable GROUP BY name HAVING COUNT(*) > 1"
        ") AS duplicate_name"
    )
    blank_names = _count(
        "SELECT COUNT(*) FROM system_variable WHERE btrim(name) = ''"
    )
    invalid_types = _count(
        "SELECT COUNT(*) FROM system_variable "
        "WHERE type NOT IN ('system', 'custom')"
    )
    invalid_value_types = _count(
        "SELECT COUNT(*) FROM system_variable "
        "WHERE var_type NOT IN ('text', 'number', 'datetime')"
    )
    invalid_values = _count(
        "SELECT COUNT(*) FROM system_variable "
        "WHERE value IS NULL OR jsonb_typeof(value) <> 'array'"
    )
    custom_variables_without_creator = _count(
        "SELECT COUNT(*) FROM system_variable "
        "WHERE type = 'custom' AND create_by IS NULL"
    )
    if any(
        (
            duplicate_names,
            blank_names,
            invalid_types,
            invalid_value_types,
            invalid_values,
            custom_variables_without_creator,
        )
    ):
        raise RuntimeError(
            "权限变量历史数据不满足一致性要求："
            f"duplicate_names={duplicate_names}, "
            f"blank_names={blank_names}, "
            f"invalid_types={invalid_types}, "
            f"invalid_value_types={invalid_value_types}, "
            f"invalid_values={invalid_values}, "
            "custom_variables_without_creator="
            f"{custom_variables_without_creator}；"
            "请先明确修复历史数据"
        )

    op.alter_column(
        "system_variable",
        "value",
        existing_type=postgresql.JSONB(),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_system_variable_type",
        "system_variable",
        "type IN ('system', 'custom')",
    )
    op.create_check_constraint(
        "ck_system_variable_var_type",
        "system_variable",
        "var_type IN ('text', 'number', 'datetime')",
    )
    op.create_check_constraint(
        "ck_system_variable_name_not_blank",
        "system_variable",
        "btrim(name) <> ''",
    )
    op.create_check_constraint(
        "ck_system_variable_value_array",
        "system_variable",
        "jsonb_typeof(value) = 'array'",
    )
    op.create_check_constraint(
        "ck_system_variable_custom_creator",
        "system_variable",
        "type = 'system' OR create_by IS NOT NULL",
    )
    op.create_unique_constraint(
        "uq_system_variable_name",
        "system_variable",
        ["name"],
    )
    op.create_index(
        "ix_system_variable_type_name",
        "system_variable",
        ["type", "name"],
    )


def downgrade() -> None:
    op.drop_index("ix_system_variable_type_name", table_name="system_variable")
    op.drop_constraint(
        "uq_system_variable_name",
        "system_variable",
        type_="unique",
    )
    op.drop_constraint(
        "ck_system_variable_custom_creator",
        "system_variable",
        type_="check",
    )
    op.drop_constraint(
        "ck_system_variable_value_array",
        "system_variable",
        type_="check",
    )
    op.drop_constraint(
        "ck_system_variable_name_not_blank",
        "system_variable",
        type_="check",
    )
    op.drop_constraint(
        "ck_system_variable_var_type",
        "system_variable",
        type_="check",
    )
    op.drop_constraint(
        "ck_system_variable_type",
        "system_variable",
        type_="check",
    )
    op.alter_column(
        "system_variable",
        "value",
        existing_type=postgresql.JSONB(),
        nullable=True,
    )
