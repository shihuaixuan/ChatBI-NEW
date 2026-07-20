"""旧 SQL 执行入口兼容层。"""

from sqlmodel import Session

from apps.capabilities.sql.execution_gateway import SqlExecuteTool
from apps.capabilities.sql.permission import PermissionTool
from apps.capabilities.sql.validator import SqlValidateTool
from apps.chatbi.services.execution.guarded_query_service import (
    QueryService,
    numeric_stats,
)
from apps.chatbi.services.execution.sql_permission import SQLPermissionService


class GuardedSqlExecutor(QueryService):
    """兼容旧类名，业务流程由 ChatBI QueryService 唯一实现。"""

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
        super().__init__(
            default_limit=default_limit,
            sample_rows=sample_rows,
            permission_service=SQLPermissionService(
                permission_tool=permission_tool
            ),
            validate_tool=validate_tool,
            execute_tool=(
                execute_tool
                if execute_tool is not None
                else SqlExecuteTool(session) if session is not None else None
            ),
        )


_numeric_stats = numeric_stats

__all__ = ["GuardedSqlExecutor", "SqlExecuteTool", "_numeric_stats"]
