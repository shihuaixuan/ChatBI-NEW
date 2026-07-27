"""产品事件的传输契约与兼容映射。"""

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel


class EventPayload(BaseModel):
    """兼容阶段 1 的旧 SSE 事件载荷。"""

    type: str
    content: Any = None
    record_id: int | None = None
    run_id: int | None = None
    sequence: int | None = None
    kind: str | None = None
    phase: str | None = None
    domain: str | None = None
    block_id: str | None = None


EventPhase: TypeAlias = Literal["start", "delta", "end", "snapshot", "error"]


class RenderEvent(EventPayload):
    """前端渲染使用的稳定事件基类。"""

    kind: str
    phase: EventPhase
    domain: str


class RunEvent(RenderEvent):
    kind: Literal["run"] = "run"
    domain: Literal["run"] = "run"


class ThinkingEvent(RenderEvent):
    kind: Literal["thinking"] = "thinking"
    domain: Literal["reasoning"] = "reasoning"


class TextEvent(RenderEvent):
    kind: Literal["text"] = "text"
    domain: Literal["answer"] = "answer"


class ToolEvent(RenderEvent):
    kind: Literal["tool"] = "tool"
    domain: Literal["tool"] = "tool"


class ArtifactEvent(RenderEvent):
    kind: Literal["artifact"] = "artifact"
    domain: Literal["artifact"] = "artifact"


class InteractionEvent(RenderEvent):
    kind: Literal["interaction"] = "interaction"
    domain: Literal["interaction"] = "interaction"


_EVENT_CONTRACT: dict[str, tuple[type[RenderEvent], EventPhase]] = {
    "record-created": (RunEvent, "start"),
    "run-started": (RunEvent, "start"),
    "step-started": (RunEvent, "start"),
    "run-finished": (RunEvent, "end"),
    "finish": (RunEvent, "end"),
    "run-failed": (RunEvent, "error"),
    "error": (RunEvent, "error"),
    "question-understood": (ThinkingEvent, "end"),
    "thinking": (ThinkingEvent, "snapshot"),
    "answer": (TextEvent, "end"),
    "tool-called": (ToolEvent, "start"),
    "workflow-step": (ToolEvent, "start"),
    "tool-result": (ToolEvent, "end"),
    "sql-generated": (ArtifactEvent, "end"),
    "sql-validated": (ArtifactEvent, "end"),
    "sql-executed": (ArtifactEvent, "end"),
    "chart-generated": (ArtifactEvent, "end"),
    "clarification": (InteractionEvent, "start"),
    "clarification-accepted": (InteractionEvent, "end"),
}


def create_render_event(
    event_type: str,
    content: Any,
    *,
    record_id: int | None,
    run_id: int,
    sequence: int,
    step_id: int | None = None,
) -> RenderEvent:
    """把旧事件类型映射为稳定渲染契约，未知类型保持可消费。"""

    event_class, phase = _EVENT_CONTRACT.get(event_type, (ArtifactEvent, "snapshot"))
    return event_class.model_validate(
        {
            "type": event_type,
            "phase": phase,
            "content": content,
            "record_id": record_id,
            "run_id": run_id,
            "sequence": sequence,
            "block_id": _block_id(event_class, event_type, content, run_id, step_id),
        }
    )


def _block_id(
    event_class: type[RenderEvent],
    event_type: str,
    content: Any,
    run_id: int,
    step_id: int | None,
) -> str | None:
    """为可增量投影的事件生成稳定块标识。"""

    if event_class is ThinkingEvent:
        return f"thinking:{step_id or run_id}"
    if event_class is TextEvent:
        return f"text:{step_id or run_id}"
    if event_class is ToolEvent:
        tool_name = content.get("tool_name") if isinstance(content, dict) else None
        return f"tool:{step_id or run_id}:{tool_name or event_type}"
    if event_class is InteractionEvent and isinstance(content, dict):
        clarification_id = content.get("clarification_id")
        return f"interaction:{clarification_id or run_id}"
    return None


# 兼容旧导入；新代码使用 RenderEvent 或具体事件类型。
AgentEventPayload = EventPayload

__all__ = [
    "AgentEventPayload",
    "ArtifactEvent",
    "EventPayload",
    "InteractionEvent",
    "RenderEvent",
    "RunEvent",
    "TextEvent",
    "ThinkingEvent",
    "ToolEvent",
    "create_render_event",
]
