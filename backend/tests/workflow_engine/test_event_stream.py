from datetime import datetime, timezone

from sqlalchemy import delete
from sqlmodel import Session

from common.core.db import engine
from sqlbot_platform.workflow_engine.domain.event import WorkflowEvent
from sqlbot_platform.workflow_engine.infrastructure.events.outbox import EventOutbox
from sqlbot_platform.workflow_engine.infrastructure.events.stream import EventStream
from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    WorkflowEventModel,
)


def _cleanup(session: Session) -> None:
    session.execute(delete(WorkflowEventModel).where(WorkflowEventModel.run_id.like("stream-%")))
    session.commit()


def _event(event_id: str, sequence: int) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=event_id,
        run_id="stream-run",
        sequence=sequence,
        event_type="node.succeeded",
        node_name="start",
        public_payload={"message": f"event-{sequence}", "sql": "select secret"},
        created_at=datetime.now(timezone.utc),
    )


def test_event_stream_lists_events_after_sequence_without_internal_payload():
    with Session(engine) as session:
        _cleanup(session)
        outbox = EventOutbox(session)
        outbox.append(_event("stream-event-1", 1), internal_payload={"trace": "secret-1"})
        outbox.append(_event("stream-event-2", 2), internal_payload={"trace": "secret-2"})
        outbox.append(_event("stream-event-3", 3), internal_payload={"trace": "secret-3"})
        session.commit()

        events = EventStream(session).list(run_id="stream-run", after_sequence=1)

        assert [event.sequence for event in events] == [2, 3]
        assert [event.public_payload for event in events] == [
            {"message": "event-2"},
            {"message": "event-3"},
        ]
        assert all(event.internal_payload_ref is None for event in events)
        _cleanup(session)
