from apps.agentic_chat.schemas import ToolResult


class SqlExampleTool:
    name = "sql_example.search"

    def run(self, payload: dict) -> ToolResult:
        return ToolResult(success=True, payload={"items": []})
