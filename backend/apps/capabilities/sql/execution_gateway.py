from typing import Any

from sqlmodel import Session

from apps.capabilities.schemas import ToolResult
from apps.datasource.composition import build_datasource_connection_service
from apps.datasource.services import DatasourceNotFoundError


class SqlExecuteTool:
    """Datasource 受控查询服务的 SQL 执行适配器。"""

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
        except Exception as exc:
            return ToolResult(
                success=False,
                error_code="sql_execute_error",
                message=str(exc),
            )


__all__ = ["SqlExecuteTool"]
