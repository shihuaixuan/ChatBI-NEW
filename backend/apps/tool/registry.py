"""工具白名单注册与分发。"""

from __future__ import annotations

from typing import Any

from apps.tool.base import Tool
from apps.tool.middleware import apply_middleware
from apps.tool.output import ToolOutput


class ToolRegistry:
    """工具白名单。未注册工具的调用一律拒绝并以错误 ToolOutput 回写。"""

    def __init__(self, middlewares: list[Any] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._middlewares: list[Any] = list(middlewares or [])

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def tool_specs(self, allowed: list[str] | None = None) -> list[dict]:
        """导出 function-calling schema；allowed 非空时仅暴露子集。"""

        tools = list(self._tools.values())
        if allowed is not None:
            allow = set(allowed)
            tools = [tool for tool in tools if tool.name in allow]
        return [tool.tool_spec() for tool in tools]

    def execute(self, name: str, ctx: Any, raw_args: dict) -> ToolOutput:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutput.denied(
                f"工具 {name} 不在白名单内，禁止调用。可用工具: {', '.join(self._tools)}",
                error_code="tool_not_allowed",
            )
        try:
            args = tool.args_model.model_validate(raw_args or {})
        except Exception as exc:
            return ToolOutput.denied(
                f"工具 {name} 参数不合法: {exc}",
                error_code="invalid_tool_args",
            )

        def call() -> ToolOutput:
            return tool.execute(ctx, args)

        return apply_middleware(self._middlewares, tool, ctx, args, call)
