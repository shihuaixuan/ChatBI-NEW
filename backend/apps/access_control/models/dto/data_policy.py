"""行列权限解析后的稳定数据策略 DTO。"""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, Field

from apps.access_control.models.dto.access_variable import (
    AccessVariableValue,
    UserVariableAssignment,
)

PolicyLogic: TypeAlias = Literal["and", "or"]


class DataPolicySubject(BaseModel):
    user_id: int
    workspace_id: int
    is_system_admin: bool = False
    name: str
    account: str
    email: str
    variable_assignments: list[UserVariableAssignment] = Field(default_factory=list)


class DataPolicyPredicate(BaseModel):
    field_id: int
    field_name: str
    operator: str
    values: list[AccessVariableValue] = Field(default_factory=list)


class DataPolicyExpression(BaseModel):
    logic: PolicyLogic
    items: list[DataPolicyPredicate | DataPolicyExpression]


class DataPolicyRowFilter(BaseModel):
    table_id: int
    table: str
    expressions: list[DataPolicyExpression]
    condition: str


class DataPolicyDeniedColumn(BaseModel):
    table_id: int
    table: str
    field_id: int
    column: str


class DataPolicy(BaseModel):
    allowed: bool = True
    reason: str = "permission_applied"
    error_code: str | None = None
    row_filters: list[DataPolicyRowFilter] = Field(default_factory=list)
    denied_columns: list[DataPolicyDeniedColumn] = Field(default_factory=list)


class StoredDataPermission(BaseModel):
    id: int
    permission_type: str
    datasource_id: int
    table_id: int
    expression_tree: str | None = None
    permissions: str | None = None
    white_list_user: str | None = None


class StoredDataRule(BaseModel):
    id: int
    workspace_id: int
    permission_list: str | None = None
    user_list: str | None = None
    white_list_user: str | None = None
