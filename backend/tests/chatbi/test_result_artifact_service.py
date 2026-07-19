import pytest

from apps.chatbi.models import (
    ChatBIResultArtifactRef,
    ChatRecordExecutionType,
    ResultArtifactWriteData,
)
from apps.chatbi.services import ResultArtifactService, ResultArtifactWriteError


class RecordingArtifactGateway:
    def __init__(self, *, fail_write: bool = False) -> None:
        self.fail_write = fail_write
        self.put_calls = []
        self.cleanup_calls = []
        self.process_calls = 0

    def put_json(self, run_id, kind, payload, metadata=None):
        if self.fail_write:
            raise OSError("storage unavailable")
        self.put_calls.append(
            {
                "run_id": run_id,
                "kind": kind,
                "payload": payload,
                "metadata": metadata,
            }
        )
        return ChatBIResultArtifactRef(
            artifact_id="artifact-1",
            kind=kind,
            content_type="application/json",
            size=20,
            digest="sha256:test",
            metadata=metadata or {},
        )

    def schedule_cleanup(self, *, metadata, execution_ids=None):
        self.cleanup_calls.append((metadata, execution_ids))
        return 2

    def process_pending_cleanup(self):
        self.process_calls += 1
        return 2


def test_save_adds_stable_execution_ownership_and_protects_reserved_metadata():
    gateway = RecordingArtifactGateway()
    service = ResultArtifactService(gateway)

    result = service.save(
        ResultArtifactWriteData(
            execution_id="agent:10",
            execution_type=ChatRecordExecutionType.AGENT,
            chat_id=20,
            record_id=30,
            kind="sql_result",
            payload={"fields": ["amount"], "rows": [{"amount": 10}]},
            metadata={
                "query_id": "query-0",
                "chat_id": 999,
                "execution_type": "graph",
            },
        )
    )

    assert result.artifact_id == "artifact-1"
    assert gateway.put_calls == [
        {
            "run_id": "agent:10",
            "kind": "sql_result",
            "payload": {"fields": ["amount"], "rows": [{"amount": 10}]},
            "metadata": {
                "query_id": "query-0",
                "execution_id": "agent:10",
                "execution_type": "agent",
                "chat_id": 20,
                "record_id": 30,
            },
        }
    ]


def test_save_converts_storage_failure_to_stable_error():
    service = ResultArtifactService(RecordingArtifactGateway(fail_write=True))

    with pytest.raises(ResultArtifactWriteError, match="RESULT_ARTIFACT_WRITE_FAILED"):
        service.save(
            ResultArtifactWriteData(
                execution_id="graph-1",
                execution_type=ChatRecordExecutionType.GRAPH,
                kind="sql_result",
                payload={},
            )
        )


def test_chat_cleanup_uses_chat_metadata_and_legacy_graph_execution_ids():
    gateway = RecordingArtifactGateway()
    service = ResultArtifactService(gateway)

    scheduled = service.schedule_chat_cleanup(
        20,
        legacy_execution_ids=["graph-legacy"],
    )
    processed = service.process_pending_cleanup()

    assert scheduled == 2
    assert processed == 2
    assert gateway.cleanup_calls == [
        ({"chat_id": 20}, ["graph-legacy"])
    ]
    assert gateway.process_calls == 1
