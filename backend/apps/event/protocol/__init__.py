"""事件协议适配。"""

from apps.event.protocol.sse import encode_sse_event, encode_sse_events

__all__ = ["encode_sse_event", "encode_sse_events"]
