from apps.agentic_chat.schemas import ToolResult


class AnswerGenerateTool:
    name = "answer.generate"

    def run(self, payload: dict) -> ToolResult:
        summary = payload.get("execution_result_summary") or {}
        row_count = summary.get("row_count", 0)
        fields = ", ".join(summary.get("fields") or [])
        content = f"查询完成，共返回 {row_count} 行数据。"
        if fields:
            content += f"字段：{fields}。"
        return ToolResult(success=True, payload={"answer": content, "chart": {}})
