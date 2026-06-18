from datetime import datetime, timezone

from sqlmodel import Session, select

from apps.workflow_engine.domain.event import WorkflowEvent
from apps.workflow_engine.infrastructure.events.outbox import _sanitize_public_payload
from apps.workflow_engine.infrastructure.persistence.models import WorkflowEventModel


class EventStream:
    """Workflow 事件续传读取器。

    该读取器只面向客户端公开事件，不返回 `internal_payload`，也不返回内部载荷引用。
    客户端用最后收到的 sequence 作为 `after_sequence`，即可从断点后继续读取。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, run_id: str, after_sequence: int = 0, limit: int = 100) -> list[WorkflowEvent]:
        models = self._session.exec(
            select(WorkflowEventModel)
            .where(WorkflowEventModel.run_id == run_id, WorkflowEventModel.sequence > after_sequence)
            .order_by(WorkflowEventModel.sequence)
            .limit(limit)
        ).all()
        return [
            WorkflowEvent(
                event_id=model.event_id,
                run_id=model.run_id,
                sequence=model.sequence,
                event_type=model.event_type,
                node_name=model.node_name,
                node_execution_id=model.node_execution_id,
                public_payload=_sanitize_public_payload(model.public_payload),
                internal_payload_ref=None,
                created_at=model.created_at or datetime.now(timezone.utc),
            )
            for model in models
        ]
