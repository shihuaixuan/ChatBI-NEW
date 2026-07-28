"""内置 Tool 中间件：超时和内部耗时统计。

领域硬门（问题理解 / 资产来源 / finish 门）不得放入 middleware。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Protocol

from apps.tool.base import Tool
from apps.tool.context import ToolCallContext
from apps.tool.result import ToolErrorCategory, ToolResult


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


class TimeoutMiddleware:
    """单工具墙钟超时。"""

    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self.timeout_seconds = timeout_seconds

    def around(
        self,
        tool: Tool,
        ctx: ToolCallContext,
        args: Any,
        call: Callable[[], ToolResult[Any]],
    ) -> ToolResult[Any]:
        if self.timeout_seconds <= 0:
            return call()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(call)
            try:
                return future.result(timeout=self.timeout_seconds)
            except FuturesTimeoutError:
                return ToolResult.interrupted(
                    f"工具 {tool.name} 执行超时（>{self.timeout_seconds}s）",
                    error_code="tool_timeout",
                    error_category=ToolErrorCategory.TIMEOUT,
                    metadata={"underlying_operation_may_still_run": True},
                )


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


def default_middlewares(
    *,
    timeout_seconds: float = 60.0,
) -> list[Any]:
    """默认横切链：Timeout → Latency → Tool；未知异常交给宿主。"""

    return [
        TimeoutMiddleware(timeout_seconds=timeout_seconds),
        LatencyMiddleware(),
    ]
