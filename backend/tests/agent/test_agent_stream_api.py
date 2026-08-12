import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import orjson
import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from apps.chatbi.api import interactions as api
from apps.chatbi.models import (
    AgentClarificationRequest,
    AgentClarificationStatus,
    AgentResumeStreamRequest,
    AgentRunStatus,
    AgentStartStreamRequest,
    ChatbiAgentClarification,
    ChatbiAgentRun,
    ChatbiAgentTraceNode,
)
from apps.chatbi.orchestration.agent import service
from apps.chatbi.services.trace_projection import project_agent_trace
from apps.conversation.models import ChatRecord
from apps.event import create_render_event
from apps.trace import TraceNodeStatus, TraceNodeType


class RecordingAccessTrace:
    def __init__(self):
        self.nodes = []

    @contextmanager
    def node(self, spec, **kwargs):
        item = {"spec": spec, **kwargs, "output": {}}
        self.nodes.append(item)

        class Handle:
            def set_output(self, output):
                item["output"] = dict(output)

        yield Handle()


async def _read_stream(response: StreamingResponse) -> str:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


def _mock_stream_session(monkeypatch, record: ChatRecord | None = None):
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def get(self, model, object_id):
            if model is ChatRecord:
                return record
            return None

        def add(self, obj):
            pass

        def commit(self):
            pass

    monkeypatch.setattr(service, "Session", lambda _engine: FakeSession())


def _enable_agent(monkeypatch):
    monkeypatch.setattr(service.settings, "CHAT_AGENT_ENABLED", True)
    monkeypatch.setattr(service.settings, "CHAT_AGENT_DATASOURCE_ALLOWLIST", "")


def test_unified_stream_starts_new_agent_run(monkeypatch):
    user = SimpleNamespace(id=7, oid=1)
    record = ChatRecord(id=3, chat_id=2, create_by=user.id, question="销售额")
    run = ChatbiAgentRun(id=5, oid=1, chat_id=2, record_id=record.id, created_by=user.id)
    captured = {}
    recorder = RecordingAccessTrace()
    _enable_agent(monkeypatch)
    _mock_stream_session(monkeypatch)

    def fake_create_record_and_run(_session, _current_user, request, _config):
        captured["request"] = request
        return record, run

    class FakeLoop:
        def __init__(self, session, current_user, config, **kwargs):
            pass

        def run(self, run_obj, record_obj):
            yield create_render_event(
                "run-started",
                {},
                record_id=record_obj.id,
                run_id=run_obj.id,
                sequence=1,
            )

    monkeypatch.setattr(service, "create_record_and_run", fake_create_record_and_run)
    monkeypatch.setattr(service, "build_agent_trace_recorder", lambda: recorder)
    monkeypatch.setattr(service, "build_agent_loop", FakeLoop)

    response = asyncio.run(
        api.agent_stream(
            user,
            AgentStartStreamRequest(
                action="start",
                chat_id=2,
                question="销售额",
                datasource_id=4,
            ),
        )
    )
    body = asyncio.run(_read_stream(response))

    assert response.media_type == "text/event-stream"
    assert captured["request"].question == "销售额"
    payload = orjson.loads(body.removeprefix("data:").strip())
    assert payload["domain"] == "run.started"
    assert (payload["kind"], payload["phase"], payload["domain"]) == (
        "run",
        "start",
        "run.started",
    )
    assert len(recorder.nodes) == 1
    access = recorder.nodes[0]
    assert access["spec"].name == "request_access"
    assert access["spec"].node_key == "request_access:initial"
    assert access["input_data"] == {"chat_id": 2, "datasource_id": 4}
    assert access["input_detail"] == {"question": "销售额"}
    assert access["output"]["conversation_owned"] is True
    assert access["output"]["datasource_allowed"] is True


def test_unified_stream_resumes_pending_clarification(monkeypatch):
    user = SimpleNamespace(id=7, oid=1)
    record = ChatRecord(id=3, chat_id=2, create_by=user.id, question="销售额")
    run = ChatbiAgentRun(
        id=5,
        oid=1,
        chat_id=2,
        record_id=record.id,
        status=AgentRunStatus.WAITING_USER.value,
        created_by=user.id,
    )
    clarification = ChatbiAgentClarification(
        id=9,
        oid=1,
        run_id=run.id,
        record_id=record.id,
        status=AgentClarificationStatus.PENDING.value,
        question="请选择指标",
        options=[],
        created_by=user.id,
    )
    captured = {}
    recorder = RecordingAccessTrace()
    _enable_agent(monkeypatch)
    _mock_stream_session(monkeypatch, record)
    monkeypatch.setattr(
        service.agent_run_repository,
        "get_latest_run_by_record",
        lambda session, record_id: run,
    )
    monkeypatch.setattr(
        service.agent_run_repository,
        "get_pending_clarification",
        lambda session, record_id: clarification,
    )
    monkeypatch.setattr(service, "build_agent_trace_recorder", lambda: recorder)

    class FakeLoop:
        def __init__(self, session, current_user, config, **kwargs):
            pass

        def resume(self, run_obj, record_obj, clarification_obj, answer_text):
            captured["answer_text"] = answer_text
            yield create_render_event(
                "clarification-accepted",
                {},
                record_id=record_obj.id,
                run_id=run_obj.id,
                sequence=1,
            )

    monkeypatch.setattr(service, "build_agent_loop", FakeLoop)

    response = asyncio.run(
        api.agent_stream(
            user,
            AgentResumeStreamRequest(
                action="resume",
                record_id=record.id,
                clarification=AgentClarificationRequest(
                    selections=[{"label": "销售下单客户数", "value": "order_customer_count"}]
                ),
            ),
        )
    )
    body = asyncio.run(_read_stream(response))

    assert clarification.status == AgentClarificationStatus.ANSWERED.value
    assert clarification.answer == {
        "selections": [{"label": "销售下单客户数", "value": "order_customer_count"}],
        "text": None,
    }
    assert captured["answer_text"] == "用户澄清回答：销售下单客户数"
    assert "clarification.accepted" in body
    assert len(recorder.nodes) == 1
    access = recorder.nodes[0]
    assert access["spec"].node_key == "request_access:resume:9"
    assert access["input_detail"] == {
        "answer_text": "用户澄清回答：销售下单客户数"
    }
    assert access["output"]["access_status"] == "accepted"


def test_timeline_returns_product_events(monkeypatch):
    user = SimpleNamespace(id=7, oid=1)
    session = SimpleNamespace()
    expected = {
        "record_id": 3,
        "run_id": 5,
        "status": "finished",
        "steps": [],
        "events": [{"sequence": 1, "domain": "run.started"}],
    }
    record_service = SimpleNamespace(get_owned=lambda user_id, record_id: object())
    monkeypatch.setattr(api, "build_chat_record_service", lambda _session: record_service)
    monkeypatch.setattr(
        api.agent_run_repository,
        "build_timeline_response",
        lambda _session, record_id: expected,
    )

    timeline = asyncio.run(api.agent_timeline(session, user, 3))

    assert timeline == expected


def test_trace_returns_owned_record_call_tree(monkeypatch):
    user = SimpleNamespace(id=7, oid=1)
    session = SimpleNamespace()
    run = ChatbiAgentRun(
        id=5,
        oid=1,
        chat_id=2,
        record_id=3,
        status=AgentRunStatus.FINISHED.value,
        created_by=user.id,
        created_at=datetime(2026, 8, 12, 12, 0, 0),
    )
    root = ChatbiAgentTraceNode(
        id=10,
        run_id=5,
        parent_id=None,
        node_key="run",
        node_type="run",
        name="agent_run",
        display_name="Agent Run",
        status="running",
        sequence=1,
        started_at=datetime(2026, 8, 12, 12, 0, 0),
        finished_at=datetime(2026, 8, 12, 12, 0, 1),
    )
    owned_calls = []
    record_service = SimpleNamespace(
        get_owned=lambda user_id, record_id: owned_calls.append((user_id, record_id))
    )
    monkeypatch.setattr(api, "build_chat_record_service", lambda _session: record_service)
    monkeypatch.setattr(
        api.agent_run_repository,
        "get_latest_run_by_record",
        lambda _session, _record_id: run,
    )
    monkeypatch.setattr(
        api.agent_trace_repository,
        "list_run_nodes",
        lambda _session, _run_id: [root],
    )

    result = asyncio.run(api.agent_trace(session, user, 3))

    assert owned_calls == [(7, 3)]
    assert result.overview.status == "succeeded"
    assert result.tree[0].id == 10


def test_trace_node_detail_reads_only_node_bound_artifact(monkeypatch):
    user = SimpleNamespace(id=7, oid=1)
    session = SimpleNamespace()
    run = ChatbiAgentRun(
        id=5,
        oid=1,
        chat_id=2,
        record_id=3,
        status=AgentRunStatus.FINISHED.value,
        created_by=user.id,
    )
    node = ChatbiAgentTraceNode(
        id=10,
        run_id=5,
        parent_id=1,
        node_key="understanding:intent",
        node_type="llm",
        name="intent_recognition",
        display_name="意图识别",
        status="succeeded",
        sequence=2,
        started_at=datetime(2026, 8, 12, 12, 0, 0),
        input_artifact_ref={"artifact_id": "artifact-input-10"},
    )
    read_calls = []

    class FakeArtifactService:
        def read(self, data):
            read_calls.append(data)
            return SimpleNamespace(payload={"prompt": "识别用户意图"})

    monkeypatch.setattr(
        api,
        "build_chat_record_service",
        lambda _session: SimpleNamespace(get_owned=lambda _user_id, _record_id: object()),
    )
    monkeypatch.setattr(
        api.agent_run_repository,
        "get_latest_run_by_record",
        lambda _session, _record_id: run,
    )
    monkeypatch.setattr(
        api.agent_trace_repository,
        "get_run_node",
        lambda _session, _run_id, _node_id: node,
    )
    monkeypatch.setattr(
        api,
        "build_result_artifact_service",
        lambda _session: FakeArtifactService(),
    )

    result = asyncio.run(api.agent_trace_node_detail(session, user, 3, 10))

    assert result.input_detail == {"prompt": "识别用户意图"}
    assert result.output_detail is None
    assert read_calls[0].artifact_id == "artifact-input-10"
    assert read_calls[0].expected_metadata == {
        "run_id": 5,
        "node_id": 10,
        "side": "input",
    }


def test_trace_hides_record_existence_when_ownership_check_fails(monkeypatch):
    user = SimpleNamespace(id=7, oid=1)

    def reject(_user_id, _record_id):
        raise api.ChatRecordError("CHAT_RECORD_NOT_OWNED")

    monkeypatch.setattr(
        api,
        "build_chat_record_service",
        lambda _session: SimpleNamespace(get_owned=reject),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(api.agent_trace(SimpleNamespace(), user, 3))

    assert exc_info.value.status_code == 404


def _trace_node(
    node_id: int,
    *,
    parent_id: int | None,
    sequence: int,
    node_type: TraceNodeType,
    status: TraceNodeStatus = TraceNodeStatus.SUCCEEDED,
) -> ChatbiAgentTraceNode:
    started_at = datetime(2026, 8, 12, 12, 0, sequence)
    return ChatbiAgentTraceNode(
        id=node_id,
        run_id=7,
        parent_id=parent_id,
        node_key=f"node:{node_id}",
        node_type=node_type.value,
        name=f"node_{node_id}",
        display_name=f"节点 {node_id}",
        status=status.value,
        sequence=sequence,
        started_at=started_at,
        finished_at=started_at + timedelta(milliseconds=100),
        latency_ms=100,
    )


def test_trace_projection_builds_nested_tree_and_sums_only_llm_tokens():
    run = ChatbiAgentRun(
        id=7,
        oid=1,
        chat_id=2,
        record_id=3,
        status=AgentRunStatus.FINISHED.value,
        created_at=datetime(2026, 8, 12, 12, 0, 0),
    )
    root = _trace_node(
        1,
        parent_id=None,
        sequence=1,
        node_type=TraceNodeType.RUN,
        status=TraceNodeStatus.RUNNING,
    )
    invocation = _trace_node(
        2,
        parent_id=1,
        sequence=2,
        node_type=TraceNodeType.INVOCATION,
    )
    llm = _trace_node(
        3,
        parent_id=2,
        sequence=3,
        node_type=TraceNodeType.LLM,
    )
    llm.token_usage = {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
    }
    llm.input_artifact_ref = {"artifact_id": "input-3"}

    result = project_agent_trace(run, [llm, root, invocation])

    assert result.overview.status == TraceNodeStatus.SUCCEEDED.value
    assert result.overview.total_tokens == 150
    assert result.overview.node_count == 3
    assert [item.id for item in result.tree] == [1]
    assert result.tree[0].children[0].id == 2
    assert result.tree[0].children[0].children[0].id == 3
    assert result.tree[0].children[0].children[0].has_input_detail is True


def test_trace_projection_preserves_partial_root_signal():
    run = ChatbiAgentRun(
        id=7,
        oid=1,
        chat_id=2,
        record_id=3,
        status=AgentRunStatus.FINISHED.value,
    )
    root = _trace_node(
        1,
        parent_id=None,
        sequence=1,
        node_type=TraceNodeType.RUN,
        status=TraceNodeStatus.PARTIAL,
    )
    root.metadata_json = {"lost_nodes": 2}

    result = project_agent_trace(run, [root])

    assert result.overview.status == TraceNodeStatus.PARTIAL.value
    assert result.overview.partial is True


def test_running_agent_cancel_records_request_instead_of_claiming_cancelled(
    monkeypatch,
):
    user = SimpleNamespace(id=7, oid=1)
    run = ChatbiAgentRun(
        id=5,
        oid=1,
        chat_id=2,
        record_id=3,
        status=AgentRunStatus.RUNNING.value,
        created_by=user.id,
    )
    session = SimpleNamespace(add=lambda _value: None, commit=lambda: None)
    monkeypatch.setattr(
        api.agent_run_repository,
        "get_run",
        lambda _session, _run_id: run,
    )

    response = asyncio.run(api.agent_cancel(session, user, run.id))

    assert response == {
        "run_id": run.id,
        "status": AgentRunStatus.CANCEL_REQUESTED.value,
    }
    assert run.status == AgentRunStatus.CANCEL_REQUESTED.value


def test_waiting_agent_cancel_can_reach_cancelled_immediately(monkeypatch):
    user = SimpleNamespace(id=7, oid=1)
    run = ChatbiAgentRun(
        id=5,
        oid=1,
        chat_id=2,
        record_id=3,
        status=AgentRunStatus.WAITING_USER.value,
        created_by=user.id,
    )
    transitioned = []
    record_service = SimpleNamespace(
        get_owned=lambda _user_id, _record_id: object(),
        transition=lambda record, status, **kwargs: transitioned.append(status),
    )
    session = SimpleNamespace(add=lambda _value: None, commit=lambda: None)
    monkeypatch.setattr(
        api.agent_run_repository,
        "get_run",
        lambda _session, _run_id: run,
    )
    monkeypatch.setattr(
        api,
        "build_chat_record_service",
        lambda _session: record_service,
    )

    response = asyncio.run(api.agent_cancel(session, user, run.id))

    assert response["status"] == AgentRunStatus.CANCELLED.value
    assert transitioned == ["cancelled"]
