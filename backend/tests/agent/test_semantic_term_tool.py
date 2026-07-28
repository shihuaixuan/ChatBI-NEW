from types import SimpleNamespace

import pytest

from apps.chatbi.models import AgentQuestionRequest
from apps.chatbi.orchestration.agent.service import create_record_and_run
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.conversation.models import Chat
from apps.semantic.models.dto import TermSearchResult
from apps.tool import ToolStatus
from apps.tool.tools.semantic import (
    SearchTerminologyArgs,
    SearchTerminologyTool,
)


class _TermQueryService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str, int]] = []

    def search(
        self,
        oid: int,
        dataset_id: int,
        query: str,
        limit: int = 10,
    ) -> list[TermSearchResult]:
        self.calls.append((oid, dataset_id, query, limit))
        return [
            TermSearchResult(
                term_id=7,
                dataset_id=dataset_id,
                words=["销售额", "GMV"],
                description="支付成功订单金额",
            )
        ]


def test_search_terminology_uses_semantic_dataset_service():
    service = _TermQueryService()
    context = AgentToolContext(
        session=object(),
        oid=1,
        user_id=2,
        datasource_id=3,
        dataset_id=20,
    )

    result = SearchTerminologyTool(service).execute(
        context,
        SearchTerminologyArgs(term="GMV"),
    )

    assert result.status == ToolStatus.SUCCEEDED
    assert result.data is not None
    assert result.data.count == 1
    assert result.data.items[0]["term_id"] == 7
    assert service.calls == [(1, 20, "GMV", 10)]


def test_search_terminology_requires_semantic_dataset():
    context = AgentToolContext(
        session=object(),
        oid=1,
        user_id=2,
        datasource_id=3,
    )

    result = SearchTerminologyTool(_TermQueryService()).execute(
        context,
        SearchTerminologyArgs(term="GMV"),
    )

    assert result.status == ToolStatus.FAILED
    assert result.error_code == "semantic_dataset_not_found"


def test_agent_record_inherits_chat_semantic_dataset():
    chat = Chat(
        id=3,
        oid=1,
        create_by=2,
        dataset_id=20,
        datasource=30,
        engine_type="PostgreSQL",
    )
    session = _RecordSession(chat)

    record, _run = create_record_and_run(
        session,
        SimpleNamespace(id=2, oid=1),
        AgentQuestionRequest(chat_id=3, question="GMV"),
        {},
    )

    assert record.dataset_id == 20
    assert record.datasource == 30


def test_agent_record_rejects_datasource_outside_conversation_binding():
    chat = Chat(
        id=3,
        oid=1,
        create_by=2,
        dataset_id=20,
        datasource=30,
        engine_type="PostgreSQL",
    )

    with pytest.raises(ValueError, match="CHAT_DATASOURCE_MISMATCH"):
        create_record_and_run(
            _RecordSession(chat),
            SimpleNamespace(id=2, oid=1),
            AgentQuestionRequest(
                chat_id=3,
                question="GMV",
                datasource_id=31,
            ),
            {},
        )


class _RecordSession:
    def __init__(self, chat: Chat) -> None:
        self.chat = chat
        self.next_id = 100

    def get(self, model, entity_id):
        if model is Chat and entity_id == self.chat.id:
            return self.chat
        return None

    def add(self, entity):
        if getattr(entity, "id", None) is None:
            entity.id = self.next_id
            self.next_id += 1

    def flush(self):
        pass

    def refresh(self, _entity):
        pass

    def commit(self):
        pass
