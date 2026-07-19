from typing import Any

from sqlmodel import Session

from apps.capabilities.schemas import ToolResult
from apps.capabilities.sql.permission import PermissionTool
from apps.capabilities.sql.validator import SqlValidateTool
from apps.datasource.composition import build_datasource_connection_service
from apps.datasource.services import DatasourceNotFoundError


class SqlExecuteTool:
    name = "sql.execute"

    def __init__(self, session: Session) -> None:
        self._service = build_datasource_connection_service(session)

    def run(self, payload: dict[str, Any]) -> ToolResult:
        datasource_id = payload.get("datasource_id")
        sql = payload.get("sql")
        if not isinstance(datasource_id, int) or not datasource_id:
            return ToolResult(
                success=False, error_code="datasource_not_found", message="数据源不存在"
            )
        if not isinstance(sql, str) or not sql.strip():
            return ToolResult(
                success=False,
                error_code="sql_execute_error",
                message="SQL 不能为空",
            )
        try:
            return ToolResult(
                success=True,
                payload=self._service.execute_query(
                    datasource_id,
                    sql,
                    origin_column=False,
                ),
            )
        except DatasourceNotFoundError:
            return ToolResult(
                success=False,
                error_code="datasource_not_found",
                message="数据源不存在",
            )
        except Exception as exc:
            return ToolResult(
                success=False, error_code="sql_execute_error", message=str(exc)
            )


class GuardedSqlExecutor:
    """守护链执行器：权限改写 → 只读校验 → 执行 → 采样与统计摘要。

    供 Agentic `execute_sql` 工具使用；full_data 供调用方落 ChatRecord.data，
    不应进入 LLM 上下文。
    """

    def __init__(
        self,
        session: Session | None,
        *,
        default_limit: int = 100,
        sample_rows: int = 10,
        permission_tool: PermissionTool | None = None,
        validate_tool: SqlValidateTool | None = None,
        execute_tool: SqlExecuteTool | None = None,
    ) -> None:
        self._permission = permission_tool or PermissionTool()
        self._validator = validate_tool or SqlValidateTool(default_limit=default_limit)
        if execute_tool is not None:
            self._executor = execute_tool
        elif session is not None:
            self._executor = SqlExecuteTool(session)
        else:
            raise ValueError("未提供 SQL 执行器或数据库会话")
        self._sample_rows = max(sample_rows, 0)

    def run(
        self,
        *,
        sql: str,
        datasource_id: int,
        allowed_tables: list[str] | None = None,
    ) -> ToolResult:
        permitted = self._permission.run({"sql": sql})
        if not permitted.success:
            return permitted
        validated = self._validator.run(
            {"sql": permitted.payload["sql"], "allowed_tables": allowed_tables or []}
        )
        if not validated.success:
            return validated
        final_sql = validated.payload["sql"]
        executed = self._executor.run(
            {"sql": final_sql, "datasource_id": datasource_id}
        )
        if not executed.success:
            return executed
        fields = executed.payload.get("fields") or []
        data = executed.payload.get("data") or []
        return ToolResult(
            success=True,
            payload={
                "sql": final_sql,
                "fields": fields,
                "sample_rows": data[: self._sample_rows],
                "row_count": len(data),
                "stats_summary": _numeric_stats(fields, data),
                "full_data": data,
            },
        )


def _numeric_stats(
    fields: list[str], rows: list[dict[str, Any]]
) -> dict[str, dict[str, float]]:
    """数值列的 min/max/sum/avg 摘要，供 LLM 在不见全量数据时判断结果形态。"""

    stats: dict[str, dict[str, float]] = {}
    for field in fields:
        values = []
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
