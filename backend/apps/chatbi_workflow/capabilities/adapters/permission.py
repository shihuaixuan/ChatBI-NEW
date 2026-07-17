from __future__ import annotations

from typing import Any, Protocol

import sqlglot
from sqlglot import exp

from apps.chatbi_capabilities.schemas import ToolResult
from apps.chatbi_capabilities.sql.permission import PermissionTool


class PermissionPolicyProvider(Protocol):
    """权限策略提供者协议，用于接入真实行列权限。"""

    def get_policy(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class PermissionAdapter:
    """ChatBI v1 权限适配器，封装 SQL 权限改写/拒绝能力。"""

    def __init__(
        self,
        permission_tool: PermissionTool | None = None,
        policy_provider: PermissionPolicyProvider | None = None,
    ) -> None:
        self._permission_tool = permission_tool or PermissionTool()
        self._policy_provider = policy_provider

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        sql = str(payload.get("sql") or "").strip()
        datasource_id = payload.get("datasource_id")
        policy = self._get_policy(
            {
                "datasource_id": datasource_id,
                "tenant_id": payload.get("tenant_id"),
                "user_id": payload.get("user_id"),
            }
        )
        if not policy.get("allowed", True):
            return {
                "allowed": False,
                "reason": str(policy.get("reason") or "权限校验拒绝"),
                "sql": None,
                "error_code": str(policy.get("error_code") or "permission_denied"),
            }
        column_denied = self._column_denial(sql, policy.get("denied_columns") or [])
        if column_denied is not None:
            return column_denied
        sql = self._apply_row_filters(sql, policy.get("row_filters") or [])

        result = self._permission_tool.run(
            {
                "sql": sql,
                "datasource_id": datasource_id,
                "tenant_id": payload.get("tenant_id"),
                "user_id": payload.get("user_id"),
            }
        )
        return self._tool_result(sql, result)

    def _get_policy(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._policy_provider is None:
            return {"allowed": True, "row_filters": [], "denied_columns": []}
        policy = self._policy_provider.get_policy(payload)
        if not isinstance(policy, dict):
            return {"allowed": False, "reason": "权限策略格式错误", "error_code": "permission_policy_invalid"}
        return policy

    def _tool_result(self, original_sql: str, result: ToolResult) -> dict[str, Any]:
        if not result.success:
            return {
                "allowed": False,
                "reason": result.message or "权限校验拒绝",
                "sql": None,
                "error_code": result.error_code or "permission_denied",
            }
        permitted = result.payload or {}
        return {
            "allowed": True,
            "reason": "permission_applied",
            "sql": permitted.get("sql") or original_sql,
            "error_code": None,
        }

    def _apply_row_filters(self, sql: str, row_filters: list[Any]) -> str:
        filters = [item for item in row_filters if isinstance(item, dict)]
        if not filters:
            return sql
        try:
            expression = sqlglot.parse_one(sql)
        except Exception as exc:
            raise ValueError("PERMISSION_SQL_PARSE_FAILED") from exc
        for row_filter in filters:
            table = str(row_filter.get("table") or "").strip()
            condition = str(row_filter.get("condition") or row_filter.get("filter") or "").strip()
            if not table or not condition:
                continue
            alias = self._table_alias(expression, table)
            if alias is None:
                continue
            expression = expression.where(
                self._qualified_condition(condition, alias),
                copy=True,
            )
        return expression.sql()

    @staticmethod
    def _table_alias(expression: exp.Expression, table_name: str) -> str | None:
        for table in expression.find_all(exp.Table):
            if table.name == table_name:
                return table.alias_or_name
        return None

    @staticmethod
    def _qualified_condition(condition: str, alias: str) -> exp.Expression:
        condition_expression = sqlglot.parse_one(condition, into=exp.Condition)

        def qualify_column(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Column) and not node.table:
                return exp.column(node.name, table=alias)
            return node

        return condition_expression.transform(qualify_column)

    def _column_denial(self, sql: str, denied_columns: list[Any]) -> dict[str, Any] | None:
        denied = self._denied_column_names(denied_columns)
        if not denied:
            return None
        try:
            expression = sqlglot.parse_one(sql)
        except Exception as exc:
            raise ValueError("PERMISSION_SQL_PARSE_FAILED") from exc
        for column in expression.find_all(exp.Column):
            column_name = column.name.lower()
            if column_name not in denied:
                continue
            return {
                "allowed": False,
                "reason": f"字段 {column.name} 无访问权限",
                "sql": None,
                "error_code": "column_permission_denied",
            }
        return None

    @staticmethod
    def _denied_column_names(denied_columns: list[Any]) -> set[str]:
        names: set[str] = set()
        for item in denied_columns:
            if isinstance(item, dict):
                value = str(item.get("column") or item.get("name") or "").strip()
            else:
                value = str(item or "").strip()
            if value:
                names.add(value.lower())
        return names
