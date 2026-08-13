"""SSE 事件编码。"""

from collections.abc import Iterable, Iterator
from contextvars import Context, copy_context
from typing import Any

import orjson

from apps.event.models.dto import RenderEvent


def encode_sse_event(payload: RenderEvent | dict[str, Any]) -> str:
    """保持现有 ``data:{json}\n\n`` 帧格式。"""

    data = payload.model_dump(exclude_none=True) if isinstance(payload, RenderEvent) else payload
    return "data:" + orjson.dumps(data).decode() + "\n\n"


def encode_sse_events(events: Iterable[RenderEvent]) -> Iterator[str]:
    """在稳定执行上下文中读取业务事件并编码为 SSE 帧。

    Starlette 会把同步迭代器的多次 ``next`` 调度到不同工作线程。业务生成器
    使用 ContextVar 传播调用上下文时，必须复用同一个 Context；这只保证迭代
    语义，不让 SSE 感知或控制 Trace。
    """

    for event in _ContextBoundIterator(events):
        yield encode_sse_event(event)


class _ContextBoundIterator(Iterator[RenderEvent]):
    """让同一个同步事件迭代器的每次 ``next`` 复用创建时的 Context。"""

    def __init__(self, events: Iterable[RenderEvent]) -> None:
        self._events = iter(events)
        self._context: Context = copy_context()

    def __next__(self) -> RenderEvent:
        return self._context.run(next, self._events)

__all__ = ["encode_sse_event", "encode_sse_events"]
