import asyncio
from types import SimpleNamespace

from fastapi.responses import StreamingResponse

from apps.agent import api, service
from apps.agent.models import (
    AgentClarificationStatus,
    AgentRunStatus,
    ChatbiAgentClarification,
    ChatbiAgentRun,
)
from apps.agent.schemas import (
    AgentClarificationRequest,
    AgentResumeStreamRequest,
    AgentStartStreamRequest,
)
from apps.chatbi.models import ChatRecord


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

    monkeypatch.setattr(api, "Session", lambda _engine: FakeSession())
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
        def __init__(self, session, current_user, config):
            pass

        def run(self, run_obj, record_obj):
            yield 'data:{"type":"run-started"}\n\n'

    monkeypatch.setattr(service.crud, "create_record_and_run", fake_create_record_and_run)
    monkeypatch.setattr(service, "AgentLoop", FakeLoop)

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
    assert "run-started" in body


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
    monkeypatch.setattr(api.crud, "get_latest_run_by_record", lambda session, record_id: run)
    monkeypatch.setattr(
        api.crud,
        "get_pending_clarification",
        lambda session, record_id: clarification,
    )

    class FakeLoop:
        def __init__(self, session, current_user, config):
            pass

        def resume(self, run_obj, record_obj, clarification_obj, answer_text):
            captured["answer_text"] = answer_text
            yield 'data:{"type":"clarification-accepted"}\n\n'

    monkeypatch.setattr(api, "AgentLoop", FakeLoop)

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
    assert "clarification-accepted" in body
