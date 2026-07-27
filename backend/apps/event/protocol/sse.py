"""SSE 事件编码。"""

from collections.abc import Iterable, Iterator

import orjson

from apps.event.models.dto import EventPayload


def encode_sse_event(payload: EventPayload | dict) -> str:
    """保持现有 ``data:{json}\n\n`` 帧格式。"""

    data = payload.model_dump() if isinstance(payload, EventPayload) else payload
    return "data:" + orjson.dumps(data).decode() + "\n\n"


def encode_sse_events(events: Iterable[EventPayload]) -> Iterator[str]:
    """在传输边界将结构化事件编码为 SSE 帧。"""

    for event in events:
        yield encode_sse_event(event)


# 兼容旧函数名称；新代码统一使用 encode_sse_event。
sse_event = encode_sse_event

__all__ = ["encode_sse_event", "encode_sse_events", "sse_event"]
