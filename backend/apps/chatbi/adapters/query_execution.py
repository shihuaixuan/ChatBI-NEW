from __future__ import annotations

from typing import Any, cast

from apps.chatbi.models.dto.tool_result import ToolResult
from apps.chatbi.services.execution import (
    GuardedQueryService,
    SQLPermissionService,
)
from apps.datasource import DatasourceConnection
from apps.datasource.database import exec_sql
from common.error import ParseSQLResultError


class ConnectionSnapshotSQLExecutor:
    """使用当前数据源连接快照执行 QueryService 已校验的 SQL。"""

    def __init__(self, connection: DatasourceConnection) -> None:
        self._connection = connection

    def run(self, payload: dict[str, Any]) -> ToolResult:
        datasource_id = payload.get("datasource_id")
        sql = payload.get("sql")
        if (
            not isinstance(datasource_id, int)
            or datasource_id <= 0
            or datasource_id != self._connection.id
        ):
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
            result = cast(
                dict[str, Any],
                exec_sql(self._connection, sql, origin_column=False),
            )
        except ParseSQLResultError as exc:
            return ToolResult(
                success=False,
                error_code="sql_result_parse_error",
                message=str(exc),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error_code="sql_execute_error",
                message=str(exc),
            )
        return ToolResult(success=True, payload=result)


def build_legacy_chat_query_service(
    connection: DatasourceConnection,
    *,
    enable_query_limit: bool,
) -> GuardedQueryService:
    """装配旧 Chat 使用的统一查询入口。"""

    return GuardedQueryService(
        default_limit=1000 if enable_query_limit else None,
        sample_rows=0,
        # 旧 Chat 在进入执行阶段前已经通过统一权限 SQL 生成服务完成改写。
        permission_service=SQLPermissionService(),
        execute_tool=ConnectionSnapshotSQLExecutor(connection),
    )


__all__ = [
    "ConnectionSnapshotSQLExecutor",
    "build_legacy_chat_query_service",
]
