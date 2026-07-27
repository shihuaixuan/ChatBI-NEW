"""事件发布服务。"""

from apps.event.models.orm import EventLog
from apps.event.repository import sqlmodel as event_repository


class EventPublisher:
    """统一执行事件序号分配、持久化和提交。"""

    def __init__(self, session) -> None:
        self._session = session

    def publish(
        self,
        run_id: int,
        event_type: str,
        payload: dict,
        step_id: int | None = None,
    ) -> EventLog:
        """提交事件并返回包含持久化序号的记录。"""

        event = event_repository.append_event(
            self._session,
            run_id,
            event_type,
            payload,
            step_id=step_id,
        )
        self._session.commit()
        return event


__all__ = ["EventPublisher"]
