"""事件对外载荷。

阶段 1 保持现有 SSE JSON 契约；强类型 RenderEvent 在后续契约升级阶段引入。
"""

from typing import Any

from pydantic import BaseModel


class EventPayload(BaseModel):
    """兼容现有 Agent SSE 的事件载荷。"""

    type: str
    content: Any = None
    record_id: int | None = None
    run_id: int | None = None
    sequence: int | None = None


# 阶段 2 的内部渲染事件名称；阶段 3 再收紧为按 type 区分的联合类型。
RenderEvent = EventPayload

# 兼容旧导入；新代码统一使用 EventPayload。
AgentEventPayload = EventPayload

__all__ = ["AgentEventPayload", "EventPayload", "RenderEvent"]
