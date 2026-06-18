from typing import Protocol

from apps.workflow_engine.domain.event import WorkflowEvent


class EventPublisher(Protocol):
    """运行事件追加与查询端口。"""

    def publish(self, event: WorkflowEvent) -> None: ...

    def list(self, run_id: str, after_sequence: int = 0) -> list[WorkflowEvent]: ...
