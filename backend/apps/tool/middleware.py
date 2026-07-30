"""内置 Tool 中间件：内部耗时统计。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from apps.tool.base import Tool
from apps.tool.context import ToolCallContext
from apps.tool.result import ToolResult


class ToolMiddleware(Protocol):
    def around(
        self,
        tool: Tool,
        ctx: ToolCallContext,
        args: Any,
        call: Callable[[], ToolResult[Any]],
    ) -> ToolResult[Any]: ...


def apply_middleware(
    middlewares: list[Any],
    tool: Tool,
    ctx: ToolCallContext,
    args: Any,
    call: Callable[[], ToolResult[Any]],
) -> ToolResult[Any]:
    """洋葱模型：先注册的 middleware 在最外层。"""

    if not middlewares:
        return call()

    def build(index: int) -> Callable[[], ToolResult[Any]]:
        if index >= len(middlewares):
            return call

        def nested() -> ToolResult[Any]:
            return middlewares[index].around(tool, ctx, args, build(index + 1))

        return nested

    return build(0)()


class LatencyMiddleware:
    """把耗时写入内部 metadata，不污染业务数据。"""

    def around(
        self,
        tool: Tool,
        ctx: ToolCallContext,
        args: Any,
        call: Callable[[], ToolResult[Any]],
    ) -> ToolResult[Any]:
        started = time.monotonic()
        result = call()
        latency_ms = int((time.monotonic() - started) * 1000)
        metadata = dict(result.metadata)
        metadata["latency_ms"] = latency_ms
        metadata["tool_name"] = tool.name
        metadata["tool_call_id"] = ctx.tool_call_id
        return result.with_updates(metadata=metadata)


def default_middlewares() -> list[Any]:
    """默认横切链只记录耗时；超时由真实底层执行器负责。"""

    return [LatencyMiddleware()]
