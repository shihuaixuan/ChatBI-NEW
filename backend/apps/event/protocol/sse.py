"""SSE 事件编码。"""

from collections.abc import Iterable, Iterator
from typing import Any

import orjson

from apps.event.models.dto import EventPayload


def encode_sse_event(payload: EventPayload | dict[str, Any]) -> str:
    """保持现有 ``data:{json}\n\n`` 帧格式。"""

    data = payload.model_dump() if isinstance(payload, EventPayload) else payload
    # 旧 EventPayload 不输出尚未启用的新契约空字段，保持原 SSE JSON 完全兼容。
    for field in ("kind", "phase", "domain", "block_id"):
        if data.get(field) is None:
            data.pop(field, None)
    return "data:" + orjson.dumps(data).decode() + "\n\n"


def encode_sse_events(events: Iterable[EventPayload]) -> Iterator[str]:
    """在传输边界将结构化事件编码为 SSE 帧。"""

    for event in events:
        yield encode_sse_event(event)


# 兼容旧函数名称；新代码统一使用 encode_sse_event。
sse_event = encode_sse_event

__all__ = ["encode_sse_event", "encode_sse_events", "sse_event"]
