from sqlmodel import Session

from sqlbot_platform.workflow_engine.domain.event import WorkflowEvent
from sqlbot_platform.workflow_engine.infrastructure.events.outbox import EventOutbox
from sqlbot_platform.workflow_engine.infrastructure.events.stream import EventStream


class DatabaseEventPublisher:
    """把运行时事件写入数据库 Outbox，并提供同一 Run 的事件续读。"""

    def __init__(self, session: Session, commit_on_publish: bool = False) -> None:
        self._session = session
        self._commit_on_publish = commit_on_publish
        self._outbox = EventOutbox(session)
        self._stream = EventStream(session)

    def publish(self, event: WorkflowEvent) -> None:
        self._outbox.append(event)
        if self._commit_on_publish:
            self._session.commit()

    def list(self, run_id: str, after_sequence: int = 0) -> list[WorkflowEvent]:
        return self._stream.list(run_id=run_id, after_sequence=after_sequence)
