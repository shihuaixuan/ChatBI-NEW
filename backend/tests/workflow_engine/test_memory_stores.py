from datetime import datetime, timezone

import pytest

from sqlbot_platform.workflow_engine.domain.artifact import WorkflowArtifact
from sqlbot_platform.workflow_engine.domain.context import WorkflowContext
from sqlbot_platform.workflow_engine.domain.event import WorkflowEvent
from sqlbot_platform.workflow_engine.domain.run import WorkflowRun
from sqlbot_platform.workflow_engine.infrastructure.memory import (
    InMemoryArtifactStore,
    InMemoryEventPublisher,
    InMemoryRunStore,
    RunVersionConflictError,
)


def _run() -> WorkflowRun:
    now = datetime.now(timezone.utc)
    return WorkflowRun(
        run_id="run-1",
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="sha256:def",
        current_node="start",
        context=WorkflowContext(),
        created_at=now,
        updated_at=now,
    )


def test_run_store_returns_copies_and_uses_optimistic_version():
    store = InMemoryRunStore()
    store.create(_run())
    first = store.get("run-1")
    first.current_node = "changed-locally"

    assert store.get("run-1").current_node == "start"

    saved = store.save(first.model_copy(update={"current_node": "next"}), expected_version=0)
    assert saved.version == 1
    with pytest.raises(RunVersionConflictError):
        store.save(first, expected_version=0)


def test_artifact_store_and_event_publisher_are_copy_safe_and_ordered():
    now = datetime.now(timezone.utc)
    artifact_store = InMemoryArtifactStore()
    event_publisher = InMemoryEventPublisher()
    artifact = WorkflowArtifact(
        artifact_id="artifact-1",
        run_id="run-1",
        kind="sql",
        content_type="text/sql",
        size=8,
        digest="sha256:sql",
        storage_uri="memory://artifact-1",
        created_at=now,
    )
    artifact_store.put(artifact, b"select 1")
    event_publisher.publish(
        WorkflowEvent(
            event_id="event-2",
            run_id="run-1",
            sequence=2,
            event_type="node.started",
            created_at=now,
        )
    )
    event_publisher.publish(
        WorkflowEvent(
            event_id="event-1",
            run_id="run-1",
            sequence=1,
            event_type="run.started",
            created_at=now,
        )
    )

    stored_artifact, content = artifact_store.get("artifact-1")
    events = event_publisher.list("run-1")

    assert stored_artifact.artifact_id == "artifact-1"
    assert content == b"select 1"
    assert [event.sequence for event in events] == [1, 2]
