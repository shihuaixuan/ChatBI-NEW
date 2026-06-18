from apps.agentic_chat.schemas import ToolResult


class TerminologyTool:
    name = "terminology.search"

    def run(self, payload: dict) -> ToolResult:
        return ToolResult(success=True, payload={"items": []})
