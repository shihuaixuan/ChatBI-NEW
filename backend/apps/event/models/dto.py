"""产品事件的传输契约。"""

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel

EventPhase: TypeAlias = Literal["start", "delta", "end", "snapshot", "error"]


class RenderEvent(BaseModel):
    """前端渲染使用的稳定事件基类。"""

    content: Any = None
    record_id: int | None = None
    run_id: int | None = None
    sequence: int | None = None
    kind: str
    phase: EventPhase
    domain: str
    block_id: str | None = None

class RunEvent(RenderEvent):
    kind: Literal["run"] = "run"


class ThinkingEvent(RenderEvent):
    kind: Literal["thinking"] = "thinking"


class TextEvent(RenderEvent):
    kind: Literal["text"] = "text"


class ToolEvent(RenderEvent):
    kind: Literal["tool"] = "tool"


class ArtifactEvent(RenderEvent):
    kind: Literal["artifact"] = "artifact"


class InteractionEvent(RenderEvent):
    kind: Literal["interaction"] = "interaction"


_EVENT_CONTRACT: dict[str, tuple[type[RenderEvent], EventPhase, str]] = {
    "record-created": (RunEvent, "start", "run.created"),
    "run-started": (RunEvent, "start", "run.started"),
    "step-started": (RunEvent, "start", "step.started"),
    "run-finished": (RunEvent, "end", "run.finished"),
    "run-failed": (RunEvent, "error", "run.failed"),
    "question-understood": (ThinkingEvent, "end", "question.understood"),
    "thinking": (ThinkingEvent, "snapshot", "reasoning.snapshot"),
    "answer": (TextEvent, "end", "answer.completed"),
    "tool-called": (ToolEvent, "start", "tool.called"),
    "workflow-step": (ToolEvent, "start", "workflow.step"),
    "tool-result": (ToolEvent, "end", "tool.completed"),
    "sql-generated": (ArtifactEvent, "end", "sql.generated"),
    "sql-validated": (ArtifactEvent, "end", "sql.validated"),
    "sql-executed": (ArtifactEvent, "end", "sql.executed"),
    "chart-generated": (ArtifactEvent, "end", "chart.generated"),
    "clarification": (InteractionEvent, "start", "clarification.required"),
    "clarification-accepted": (InteractionEvent, "end", "clarification.accepted"),
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
    """把内部事件名称映射为稳定渲染契约。"""

    try:
        event_class, phase, domain = _EVENT_CONTRACT[event_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported render event: {event_type}") from exc
    return event_class.model_validate(
        {
            "phase": phase,
            "domain": domain,
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


__all__ = [
    "ArtifactEvent",
    "InteractionEvent",
    "RenderEvent",
    "RunEvent",
    "TextEvent",
    "ThinkingEvent",
    "ToolEvent",
    "create_render_event",
]
