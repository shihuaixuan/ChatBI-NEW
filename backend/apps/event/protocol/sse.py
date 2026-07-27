"""SSE 事件编码。"""

from collections.abc import Iterable, Iterator
from typing import Any

import orjson

from apps.event.models.dto import RenderEvent


def encode_sse_event(payload: RenderEvent | dict[str, Any]) -> str:
    """保持现有 ``data:{json}\n\n`` 帧格式。"""

    data = payload.model_dump(exclude_none=True) if isinstance(payload, RenderEvent) else payload
    return "data:" + orjson.dumps(data).decode() + "\n\n"


def encode_sse_events(events: Iterable[RenderEvent]) -> Iterator[str]:
    """在传输边界将结构化事件编码为 SSE 帧。"""

    for event in events:
        yield encode_sse_event(event)

__all__ = ["encode_sse_event", "encode_sse_events"]
