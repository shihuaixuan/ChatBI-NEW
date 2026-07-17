from __future__ import annotations

from apps.agent.tools.base import AgentTool, AgentToolContext, ToolOutput


class ToolRegistry:
    """工具白名单。未注册工具的调用一律拒绝并以错误 ToolMessage 回写。"""

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def tool_specs(self) -> list[dict]:
        return [tool.tool_spec() for tool in self._tools.values()]

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def execute(self, name: str, ctx: AgentToolContext, raw_args: dict) -> ToolOutput:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutput(
                success=False,
                summary=f"工具 {name} 不在白名单内，禁止调用。可用工具: {', '.join(self._tools)}",
                error_code="tool_not_allowed",
            )
        try:
            args = tool.args_model.model_validate(raw_args or {})
        except Exception as exc:
            return ToolOutput(
                success=False,
                summary=f"工具 {name} 参数不合法: {exc}",
                error_code="invalid_tool_args",
            )
        return tool.execute(ctx, args)
