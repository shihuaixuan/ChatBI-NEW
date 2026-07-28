import asyncio
from types import SimpleNamespace

import orjson
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
)
from apps.chatbi.orchestration.agent import service
from apps.conversation.models import ChatRecord
from apps.event import create_render_event


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
