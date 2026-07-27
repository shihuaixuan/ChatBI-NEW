"""Agent 旧事件编码入口。

新代码应直接使用 apps.event.protocol.sse；此模块只保留兼容导出。
"""

from apps.event import encode_sse_event

sse_event = encode_sse_event

__all__ = ["sse_event"]
