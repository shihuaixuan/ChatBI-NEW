"""内置工具中间件：异常归一、超时、耗时统计。

领域硬门（问题理解 / 资产来源 / finish 门）不得放入 middleware。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Protocol

from apps.tool.base import Tool
from apps.tool.output import ToolOutput


class ToolMiddleware(Protocol):
    def around(
        self,
        tool: Tool,
        ctx: Any,
        args: Any,
        call: Callable[[], ToolOutput],
    ) -> ToolOutput: ...


def apply_middleware(
    middlewares: list[Any],
    tool: Tool,
    ctx: Any,
    args: Any,
    call: Callable[[], ToolOutput],
) -> ToolOutput:
    """洋葱模型：先注册的 middleware 在最外层。"""

    if not middlewares:
        return call()

    def build(index: int) -> Callable[[], ToolOutput]:
        if index >= len(middlewares):
            return call

        def nested() -> ToolOutput:
            return middlewares[index].around(tool, ctx, args, build(index + 1))

        return nested

    return build(0)()


class ErrorNormalizeMiddleware:
    """未捕获异常 → ToolOutput.error，避免打穿循环。"""

    def around(
        self,
        tool: Tool,
        ctx: Any,
        args: Any,
        call: Callable[[], ToolOutput],
    ) -> ToolOutput:
        try:
            return call()
        except Exception as exc:
            return ToolOutput.error(
                f"工具 {tool.name} 执行异常: {exc}",
                error_code="tool_exception",
            )


class TimeoutMiddleware:
    """单工具墙钟超时。"""

    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self.timeout_seconds = timeout_seconds

    def around(
        self,
        tool: Tool,
        ctx: Any,
        args: Any,
        call: Callable[[], ToolOutput],
    ) -> ToolOutput:
        if self.timeout_seconds <= 0:
            return call()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(call)
            try:
                return future.result(timeout=self.timeout_seconds)
            except FuturesTimeoutError:
                return ToolOutput.error(
                    f"工具 {tool.name} 执行超时（>{self.timeout_seconds}s）",
                    error_code="tool_timeout",
                )


class LatencyMiddleware:
    """把 latency_ms 写入 payload._trace（不进 summary）。"""

    def around(
        self,
        tool: Tool,
        ctx: Any,
        args: Any,
        call: Callable[[], ToolOutput],
    ) -> ToolOutput:
        started = time.monotonic()
        output = call()
        latency_ms = int((time.monotonic() - started) * 1000)
        payload = dict(output.payload or {})
        trace = dict(payload.get("_trace") or {})
        trace["latency_ms"] = latency_ms
        trace["tool_name"] = tool.name
        payload["_trace"] = trace
        output.payload = payload
        return output


def default_middlewares(
    *,
    timeout_seconds: float = 60.0,
) -> list[Any]:
    """默认横切链：外层 ErrorNormalize → Timeout → Latency → tool。"""

    return [
        ErrorNormalizeMiddleware(),
        TimeoutMiddleware(timeout_seconds=timeout_seconds),
        LatencyMiddleware(),
    ]


# 兼容旧导入；该中间件只统计耗时，不创建可观测性 Trace。
TracingMiddleware = LatencyMiddleware
