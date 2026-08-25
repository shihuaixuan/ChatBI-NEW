import pytest

from apps.chatbi.models import (
    ChatBIResultArtifactRef,
    ResultArtifactReadInput,
    ResultArtifactWriteData,
)
from apps.chatbi.services.execution import (
    ResultArtifactReadError,
    ResultArtifactService,
    ResultArtifactWriteError,
)
from apps.conversation import ChatRecordExecutionType


class RecordingArtifactGateway:
    def __init__(
        self,
        *,
        fail_write: bool = False,
        fail_read: bool = False,
    ) -> None:
        self.fail_write = fail_write
        self.fail_read = fail_read
        self.put_calls = []
        self.get_calls = []
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

    def get_json(self, artifact_id):
        if self.fail_read:
            raise OSError("storage unavailable")
        self.get_calls.append(artifact_id)
        return {
            "artifact_id": artifact_id,
            "run_id": "agent:10",
            "kind": "agent_trace_input",
            "content_type": "application/json",
            "size": 20,
            "digest": "sha256:test",
            "metadata": {
                "execution_id": "agent:10",
                "execution_type": "agent",
                "chat_id": 20,
                "record_id": 30,
                "run_id": 10,
                "node_id": 40,
                "side": "input",
            },
            "payload": {"question": "本月新增客户数"},
        }

    def find_json(self, *, run_id, kind, idempotency_key):
        return None

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


def test_named_result_set_entry_points_validate_result_set_identity():
    gateway = RecordingArtifactGateway()
    service = ResultArtifactService(gateway)
    data = ResultArtifactWriteData(
        execution_id="agent:10",
        execution_type=ChatRecordExecutionType.AGENT,
        chat_id=20,
        record_id=30,
        kind="analysis_result_set",
        payload={"result_set_id": "result:plan-1:query-1", "rows": []},
        metadata={"result_set_id": "result:plan-1:query-1"},
    )

    ref = service.save_named_result_set(
        data,
        result_set_id="result:plan-1:query-1",
    )

    assert ref.kind == "analysis_result_set"
    with pytest.raises(ValueError, match="RESULT_SET_ARTIFACT_ID_MISMATCH"):
        service.save_named_result_set(data, result_set_id="result:plan-2:query-1")


def test_read_returns_payload_only_after_execution_ownership_matches():
    gateway = RecordingArtifactGateway()
    service = ResultArtifactService(gateway)

    result = service.read(
        ResultArtifactReadInput(
            artifact_id="artifact-1",
            execution_id="agent:10",
            execution_type=ChatRecordExecutionType.AGENT,
            chat_id=20,
            record_id=30,
            kind="agent_trace_input",
            expected_metadata={"run_id": 10, "node_id": 40, "side": "input"},
        )
    )

    assert gateway.get_calls == ["artifact-1"]
    assert result.payload == {"question": "本月新增客户数"}


def test_read_rejects_artifact_from_another_node():
    service = ResultArtifactService(RecordingArtifactGateway())

    with pytest.raises(
        ResultArtifactReadError,
        match=ResultArtifactReadError.OWNERSHIP_MISMATCH,
    ):
        service.read(
            ResultArtifactReadInput(
                artifact_id="artifact-1",
                execution_id="agent:10",
                execution_type=ChatRecordExecutionType.AGENT,
                chat_id=20,
                record_id=30,
                kind="agent_trace_input",
                expected_metadata={"run_id": 10, "node_id": 41, "side": "input"},
            )
        )


def test_read_converts_storage_failure_to_stable_error():
    service = ResultArtifactService(RecordingArtifactGateway(fail_read=True))

    with pytest.raises(
        ResultArtifactReadError,
        match=ResultArtifactReadError.READ_FAILED,
    ):
        service.read(
            ResultArtifactReadInput(
                artifact_id="artifact-1",
                execution_id="agent:10",
                execution_type=ChatRecordExecutionType.AGENT,
                chat_id=20,
                record_id=30,
                kind="agent_trace_input",
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
    assert gateway.cleanup_calls == [({"chat_id": 20}, ["graph-legacy"])]
    assert gateway.process_calls == 1
