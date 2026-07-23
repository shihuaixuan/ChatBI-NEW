from datetime import datetime, timezone

import pytest
from sqlalchemy import delete
from sqlmodel import Session, select

from common.core.db import engine
from sqlbot_platform.workflow_engine.domain.event import WorkflowEvent
from sqlbot_platform.workflow_engine.infrastructure.events.outbox import EventOutbox
from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    WorkflowEventModel,
)


def _cleanup(session: Session) -> None:
    session.execute(delete(WorkflowEventModel).where(WorkflowEventModel.run_id.like("outbox-%")))
    session.commit()


def _event(event_id: str, run_id: str = "outbox-run", sequence: int = 1) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=event_id,
        run_id=run_id,
        sequence=sequence,
        event_type="node.succeeded",
        node_name="start",
        public_payload={
            "message": "节点完成",
            "sql": "select * from secret_table",
            "connection": "postgres://secret",
            "prompt": "内部提示词",
            "internal_error": "stacktrace",
        },
        created_at=datetime.now(timezone.utc),
    )


def test_event_outbox_deduplicates_event_id_and_keeps_public_payload_safe():
    with Session(engine) as session:
        _cleanup(session)
        outbox = EventOutbox(session)

        first = outbox.append(_event("outbox-event-1"), internal_payload={"trace": "secret"})
        duplicate = outbox.append(_event("outbox-event-1"), internal_payload={"trace": "new-secret"})
        session.commit()

        rows = session.exec(select(WorkflowEventModel).where(WorkflowEventModel.run_id == "outbox-run")).all()
        assert len(rows) == 1
        assert first.event_id == duplicate.event_id
        assert rows[0].public_payload == {"message": "节点完成"}
        assert rows[0].internal_payload == {"trace": "secret"}
        _cleanup(session)


def test_event_outbox_publishes_pending_events_and_retains_failed_for_retry():
    delivered: list[str] = []

    def publisher(event: WorkflowEvent) -> None:
        if event.event_id == "outbox-fail":
            raise RuntimeError("broker down")
        delivered.append(event.event_id)

    with Session(engine) as session:
        _cleanup(session)
        outbox = EventOutbox(session)
        outbox.append(_event("outbox-ok", sequence=1))
        outbox.append(_event("outbox-fail", sequence=2))
        session.commit()

        result = outbox.publish_pending(publisher=publisher, run_id="outbox-run")
        session.commit()

        ok = session.exec(select(WorkflowEventModel).where(WorkflowEventModel.event_id == "outbox-ok")).one()
        failed = session.exec(select(WorkflowEventModel).where(WorkflowEventModel.event_id == "outbox-fail")).one()
        assert result.published == 1
        assert result.failed == 1
        assert delivered == ["outbox-ok"]
        assert ok.publish_status == "published"
        assert ok.publish_attempts == 1
        assert failed.publish_status == "pending"
        assert failed.publish_attempts == 1
        _cleanup(session)


def test_event_outbox_rejects_non_monotonic_sequence_for_same_run():
    with Session(engine) as session:
        _cleanup(session)
        outbox = EventOutbox(session)
        outbox.append(_event("outbox-seq-1", sequence=2))

        with pytest.raises(ValueError, match="EVENT_SEQUENCE_NOT_MONOTONIC"):
            outbox.append(_event("outbox-seq-2", sequence=2))

        session.rollback()
        _cleanup(session)
