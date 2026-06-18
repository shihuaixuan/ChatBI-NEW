from apps.agentic_chat.schemas import ToolResult


class SqlGenerateTool:
    name = "sql.generate_schema"

    def run(self, payload: dict) -> ToolResult:
        allowed_tables = payload.get("allowed_tables") or []
        if not allowed_tables:
            return ToolResult(success=False, error_code="no_table_evidence", message="没有可用表证据")
        table = allowed_tables[0]
        # P1 最小闭环先生成保守只读 SQL，后续替换为独立 Agentic Prompt。
        return ToolResult(success=True, payload={"sql": f"select * from {table}"})
