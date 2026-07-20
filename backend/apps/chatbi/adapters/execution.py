"""Datasource 受控查询的 SQL 执行适配器（实现 execution.ports.SQLExecutor）。"""

from typing import Any

from sqlmodel import Session

from apps.chatbi.models.dto.tool_result import ToolResult
from apps.datasource.composition import build_datasource_connection_service
from apps.datasource.services import DatasourceNotFoundError


class DatasourceQueryExecutor:
    """通过 Datasource 受控连接服务执行 SQL。"""

    name = "sql.execute"

    def __init__(self, session: Session) -> None:
        self._service = build_datasource_connection_service(session)

    def run(self, payload: dict[str, Any]) -> ToolResult:
        datasource_id = payload.get("datasource_id")
        sql = payload.get("sql")
        if not isinstance(datasource_id, int) or not datasource_id:
            return ToolResult(
                success=False,
                error_code="datasource_not_found",
                message="数据源不存在",
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
        except Exception as exc:  # noqa: BLE001 - 驱动异常统一归类为执行错误
            return ToolResult(
                success=False,
                error_code="sql_execute_error",
                message=str(exc),
            )


# 旧名兼容（台账 B6）：capabilities.sql.execution_gateway 转发使用。
SqlExecuteTool = DatasourceQueryExecutor

__all__ = ["DatasourceQueryExecutor", "SqlExecuteTool"]
