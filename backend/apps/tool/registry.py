"""工具白名单注册与分发。"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from apps.tool.base import Tool
from apps.tool.context import ToolCall, ToolCallContext
from apps.tool.definition import ToolDefinition
from apps.tool.middleware import apply_middleware
from apps.tool.result import (
    RetryAdvice,
    ToolErrorCategory,
    ToolResult,
    ToolStatus,
)


class ToolRegistry:
    """Tool 白名单、参数校验、结果校验和通用调用入口。"""

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

    def definitions(self, allowed: list[str] | None = None) -> list[ToolDefinition]:
        """导出厂商无关定义；allowed 非空时仅暴露子集。"""

        tools = list(self._tools.values())
        if allowed is not None:
            allow = set(allowed)
            tools = [tool for tool in tools if tool.name in allow]
        return [tool.definition() for tool in tools]

    def execute(
        self,
        call: ToolCall,
        ctx: Any,
        *,
        call_context: ToolCallContext | None = None,
    ) -> ToolResult[Any]:
        """校验并执行一次调用；未声明异常直接向宿主传播。"""

        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult.rejected(
                f"工具 {call.name} 不在白名单内，禁止调用。可用工具: {', '.join(self._tools)}",
                error_code="tool_not_allowed",
                error_category=ToolErrorCategory.AUTHORIZATION,
            )
        try:
            args = tool.args_model.model_validate(call.args or {})
        except ValidationError as exc:
            return ToolResult.failed(
                f"工具 {call.name} 参数不合法: {exc}",
                error_code="invalid_tool_args",
                error_category=ToolErrorCategory.VALIDATION,
                retry_advice=RetryAdvice.CORRECT_INPUT,
                details={"errors": exc.errors(include_url=False)},
            )

        args_validator = type(tool).args_validator
        if args_validator is not None:
            try:
                args_validator(ctx, args)
            except ValueError as exc:
                return ToolResult.failed(
                    f"工具 {call.name} 参数不合法: {exc}",
                    error_code="invalid_tool_args",
                    error_category=ToolErrorCategory.VALIDATION,
                    retry_advice=RetryAdvice.CORRECT_INPUT,
                )

        def invoke() -> ToolResult[Any]:
            return tool.execute(ctx, args)

        result = apply_middleware(
            self._middlewares,
            tool,
            call_context or ToolCallContext(tool_call_id=call.call_id),
            args,
            invoke,
        )
        if result.status == ToolStatus.SUCCEEDED:
            if result.data is None:
                raise TypeError(f"TOOL_RESULT_DATA_REQUIRED:{tool.name}")
            validated = tool.result_model.model_validate(result.data)
            return result.with_updates(data=validated)
        return result
