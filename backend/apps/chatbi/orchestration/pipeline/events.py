"""计划、任务和计算事件的统一发布入口。"""

from __future__ import annotations

from typing import Any, Protocol

from apps.event import EventPublisher, RenderEvent


class PipelineEventSink(Protocol):
    def publish(
        self,
        run_id: int,
        event_type: str,
        payload: dict[str, Any],
        step_id: int | None = None,
    ) -> RenderEvent: ...


class PipelineEvents:
    """所有新增事件都经过这里，避免各模式自行拼接事件契约。"""

    def __init__(self, publisher: PipelineEventSink | EventPublisher) -> None:
        self._publisher = publisher

    def plan_created(self, run_id: int, payload: dict[str, Any]) -> RenderEvent:
        return self._publisher.publish(run_id, "plan-created", payload)

    def plan_updated(self, run_id: int, payload: dict[str, Any]) -> RenderEvent:
        return self._publisher.publish(run_id, "plan-updated", payload)

    def task_started(self, run_id: int, payload: dict[str, Any]) -> RenderEvent:
        return self._publisher.publish(run_id, "task-started", payload)

    def task_finished(self, run_id: int, payload: dict[str, Any]) -> RenderEvent:
        return self._publisher.publish(run_id, "task-finished", payload)

    def compute_finished(self, run_id: int, payload: dict[str, Any]) -> RenderEvent:
        return self._publisher.publish(run_id, "compute-finished", payload)


__all__ = ["PipelineEvents", "PipelineEventSink"]
