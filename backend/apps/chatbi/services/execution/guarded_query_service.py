from __future__ import annotations

from typing import Any

from apps.chatbi.models.dto.tool_result import ToolResult
from apps.chatbi.services.execution.ports import SQLExecutor
from apps.chatbi.services.execution.sql_permission import SQLPermissionService
from apps.chatbi.services.execution.sql_validator import SqlValidateTool


class GuardedQueryService:
    """Agent 与 Graph 共用的 SQL 校验、权限和执行入口。"""

    def __init__(
        self,
        *,
        default_limit: int | None = 100,
        sample_rows: int = 10,
        permission_service: SQLPermissionService | None = None,
        validate_tool: SqlValidateTool | None = None,
        execute_tool: SQLExecutor | None = None,
    ) -> None:
        self._permission_service = permission_service or SQLPermissionService()
        self._validator = validate_tool or SqlValidateTool(default_limit=default_limit)
        self._executor = execute_tool
        self._sample_rows = max(sample_rows, 0)

    def validate_sql(
        self,
        sql: str,
        *,
        allowed_tables: list[str] | None = None,
    ) -> ToolResult:
        return self._validator.run(
            {
                "sql": sql,
                "allowed_tables": allowed_tables or [],
            }
        )

    def execute_sql(
        self,
        *,
        sql: str,
        datasource_id: int,
        workspace_id: int | None,
        user_id: int | None,
        allowed_tables: list[str] | None = None,
    ) -> ToolResult:
        if self._executor is None:
            return ToolResult(
                success=False,
                error_code="sql_execute_tool_required",
                message="SQL 执行工具未配置",
            )
        try:
            permission = self._permission_service.apply(
                {
                    "sql": sql,
                    "datasource_id": datasource_id,
                    "tenant_id": workspace_id,
                    "user_id": user_id,
                }
            )
        except ValueError as exc:
            return ToolResult(
                success=False,
                error_code="permission_sql_invalid",
                message=str(exc),
            )
        if not permission.get("allowed"):
            return ToolResult(
                success=False,
                error_code=str(
                    permission.get("error_code") or "permission_denied"
                ),
                message=str(permission.get("reason") or "权限校验拒绝"),
            )
        permitted_sql = str(permission.get("sql") or sql)
        validated = self.validate_sql(
            permitted_sql,
            allowed_tables=allowed_tables,
        )
        if not validated.success:
            return validated
        final_sql = str(validated.payload.get("sql") or permitted_sql)
        executed = self._executor.run(
            {"sql": final_sql, "datasource_id": datasource_id}
        )
        if not executed.success:
            return executed
        payload = executed.payload or {}
        raw_fields = payload.get("fields") or []
        raw_data = payload.get("data") or payload.get("rows") or []
        fields = [str(field) for field in raw_fields] if isinstance(raw_fields, list) else []
        data = [row for row in raw_data if isinstance(row, dict)] if isinstance(raw_data, list) else []
        raw_row_count = payload.get("row_count")
        row_count = (
            raw_row_count
            if isinstance(raw_row_count, int) and not isinstance(raw_row_count, bool)
            else len(data)
        )
        execution_metadata = {
            key: value
            for key, value in payload.items()
            if key not in {"fields", "data", "rows"}
        }
        return ToolResult(
            success=True,
            payload={
                "sql": final_sql,
                "fields": fields,
                "sample_rows": data[: self._sample_rows],
                "row_count": row_count,
                "stats_summary": numeric_stats(fields, data),
                "full_data": data,
                "execution_ms": int(payload.get("execution_ms") or 0),
                "artifact_ref": payload.get("artifact_ref"),
                "execution_metadata": execution_metadata,
            },
        )

    def run(
        self,
        *,
        sql: str,
        datasource_id: int,
        allowed_tables: list[str] | None = None,
        workspace_id: int | None = None,
        user_id: int | None = None,
    ) -> ToolResult:
        """兼容旧守护执行器的方法名。"""

        return self.execute_sql(
            sql=sql,
            datasource_id=datasource_id,
            workspace_id=workspace_id,
            user_id=user_id,
            allowed_tables=allowed_tables,
        )


def numeric_stats(
    fields: list[str],
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """生成数值列摘要，避免把完整结果写入模型上下文。"""

    stats: dict[str, dict[str, float]] = {}
    for field in fields:
        values: list[float] = []
        for row in rows:
            value = row.get(field)
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                values.append(float(value))
        if values:
            total = sum(values)
            stats[field] = {
                "min": min(values),
                "max": max(values),
                "sum": total,
                "avg": total / len(values),
            }
    return stats


# 旧名兼容（台账 E1）。
QueryService = GuardedQueryService

__all__ = ["GuardedQueryService", "QueryService", "SQLExecutor", "numeric_stats"]
