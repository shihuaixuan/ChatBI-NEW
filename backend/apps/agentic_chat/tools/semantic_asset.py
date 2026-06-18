from apps.agentic_chat.schemas import ToolResult


class SemanticAssetTool:
    name = "semantic.search"

    def run(self, payload: dict) -> ToolResult:
        # P1 骨架先返回空命中，后续接入 semantic_search 服务。
        return ToolResult(success=True, payload={"items": [], "degraded": True, "reason": "not_enabled_in_p1"})
