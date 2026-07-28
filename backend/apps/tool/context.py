"""一次 Tool 调用的可信运行元数据。"""

from __future__ import annotations

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


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型提出的一次结构化 Tool 调用。"""

    name: str
    args: dict[str, Any]
    call_id: str


__all__ = [
    "CancellationSignal",
    "NeverCancelled",
    "ToolCall",
    "ToolCallContext",
]
