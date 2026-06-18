from apps.agentic_chat.schemas import ToolResult


class SqlExecuteTool:
    name = "sql.execute"

    def __init__(self, session):
        self.session = session

    def run(self, payload: dict) -> ToolResult:
        from apps.datasource.crud.datasource import get_ds
        from apps.db.db import exec_sql

        datasource_id = payload.get("datasource_id")
        sql = payload.get("sql")
        ds = get_ds(self.session, datasource_id) if datasource_id else None
        if not ds:
            return ToolResult(success=False, error_code="datasource_not_found", message="数据源不存在")
        try:
            return ToolResult(success=True, payload=exec_sql(ds, sql, origin_column=False))
        except Exception as exc:
            return ToolResult(success=False, error_code="sql_execute_error", message=str(exc))
