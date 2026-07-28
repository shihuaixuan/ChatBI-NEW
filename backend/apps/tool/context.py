"""一次 Tool 调用的可信运行元数据。"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol


class CancellationSignal(Protocol):
    """底层执行器可以轮询的取消信号。"""

    def is_cancelled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class NeverCancelled:
    """没有外部取消来源时使用的明确信号。"""

    def is_cancelled(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ToolCallContext:
    """通用运行时创建的调用信息，不包含模型可控业务字段。"""

    tool_call_id: str
    deadline_monotonic: float | None = None
    cancellation: CancellationSignal = field(default_factory=NeverCancelled)
    trace_context: dict[str, Any] = field(default_factory=dict)

    def remaining_seconds(self) -> float | None:
        if self.deadline_monotonic is None:
            return None
        return max(self.deadline_monotonic - time.monotonic(), 0.0)

    def deadline_exceeded(self) -> bool:
        remaining = self.remaining_seconds()
        return remaining is not None and remaining <= 0


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型提出的一次结构化 Tool 调用。"""

    name: str
    args: dict[str, Any]
    call_id: str


_CURRENT_TOOL_CALL_CONTEXT: ContextVar[ToolCallContext | None] = ContextVar(
    "current_tool_call_context",
    default=None,
)


@contextmanager
def bind_tool_call_context(context: ToolCallContext) -> Iterator[None]:
    """在当前执行上下文中绑定可信调用元数据，并支持并发线程复制。"""

    token = _CURRENT_TOOL_CALL_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_TOOL_CALL_CONTEXT.reset(token)


def current_tool_call_context() -> ToolCallContext | None:
    return _CURRENT_TOOL_CALL_CONTEXT.get()


def effective_timeout_seconds(
    *,
    declared_timeout: float | None,
    default_timeout: float,
    maximum_timeout: float,
    run_remaining: float,
) -> float:
    """按 Tool 声明、系统上限和 Run 剩余时间计算真实执行预算。"""

    requested = declared_timeout if declared_timeout is not None else default_timeout
    candidates = [requested, maximum_timeout, run_remaining]
    positive = [float(value) for value in candidates if float(value) > 0]
    return min(positive) if len(positive) == len(candidates) else 0.0


__all__ = [
    "CancellationSignal",
    "NeverCancelled",
    "ToolCall",
    "ToolCallContext",
    "bind_tool_call_context",
    "current_tool_call_context",
    "effective_timeout_seconds",
]
