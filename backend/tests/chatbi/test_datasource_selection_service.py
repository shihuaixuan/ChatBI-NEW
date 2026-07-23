from __future__ import annotations

from collections.abc import Iterator

import orjson
import pytest

from apps.chatbi.models import (
    ChatRecord,
    DatasourceSelectionCandidate,
    DatasourceSelectionData,
    DatasourceSelectionEvent,
    ModelMessage,
    ModelStreamChunk,
)
from apps.chatbi.services.conversation import ChatRecordService
from apps.chatbi.services.planning import (
    DatasourceSelectionError,
    DatasourceSelectionService,
)


class FakePromptBuilder:
    def __init__(self) -> None:
        self.data: DatasourceSelectionData | None = None

    def build(
        self,
        data: DatasourceSelectionData,
    ) -> list[ModelMessage]:
        self.data = data
        return [
            ModelMessage(role="system", content=data.language),
            ModelMessage(role="human", content=data.question),
        ]


class FakeModelClient:
    def __init__(self, chunks: list[ModelStreamChunk]) -> None:
        self.chunks = chunks
        self.messages: list[ModelMessage] | None = None

    def stream(
        self,
        messages: list[ModelMessage],
    ) -> Iterator[ModelStreamChunk]:
        self.messages = messages
        yield from self.chunks


class FakeChatRecordRepository:
    def __init__(self, record: ChatRecord) -> None:
        self.record = record
        self.saved = 0
        self.bindings: list[tuple[int, int, str]] = []

    def get(self, record_id: int) -> ChatRecord | None:
        return self.record if self.record.id == record_id else None

    def create(self, data):
        raise AssertionError("数据源选择不应创建 ChatRecord")

    def save(self, record: ChatRecord) -> None:
        self.record = record
        self.saved += 1

    def promote_recommendation(
        self,
        chat_id: int,
        *,
        answer: str,
        questions: str,
    ) -> None:
        raise AssertionError("数据源选择不应提升推荐问题")

    def bind_conversation_datasource(
        self,
        chat_id: int,
        *,
        datasource_id: int,
        engine_type: str,
    ) -> None:
        self.bindings.append((chat_id, datasource_id, engine_type))


def _candidates() -> list[DatasourceSelectionCandidate]:
    return [
        DatasourceSelectionCandidate(
            id=101,
            name="销售库",
            description="订单与销售数据",
        ),
        DatasourceSelectionCandidate(
            id=102,
            name="库存库",
            description="商品库存数据",
        ),
    ]


def _data(
    *,
    candidates: list[DatasourceSelectionCandidate] | None = None,
    auto_select: bool = False,
) -> DatasourceSelectionData:
    return DatasourceSelectionData(
        record_id=10,
        question="查询本月销售额",
        candidates=_candidates() if candidates is None else candidates,
        language="简体中文",
        assistant_name="Numora",
        auto_select=auto_select,
    )


def _service(
    model: FakeModelClient | None = None,
) -> tuple[
    DatasourceSelectionService,
    FakePromptBuilder,
    FakeModelClient,
    FakeChatRecordRepository,
]:
    prompt = FakePromptBuilder()
    model = model or FakeModelClient([])
    repository = FakeChatRecordRepository(
        ChatRecord(
            id=10,
            chat_id=20,
            create_by=30,
            question="查询本月销售额",
            execution_type="graph",
            status="created",
            finish=False,
        )
    )
    service = DatasourceSelectionService(
        prompt_builder=prompt,
        model_client=model,
        chat_record_service=ChatRecordService(repository),
    )
    return service, prompt, model, repository


def test_single_candidate_is_selected_without_prompt_or_model():
    service, prompt, model, _ = _service()
    data = _data(candidates=[_candidates()[0]], auto_select=True)

    assert service.prepare(data) == []
    events = list(service.generate(data, []))

    assert prompt.data is None
    assert model.messages is None
    assert events == [
        DatasourceSelectionEvent(
            kind="completed",
            selected_datasource_id=101,
            model_used=False,
        )
    ]


def test_model_selection_streams_and_parses_first_valid_object():
    model = FakeModelClient(
        [
            ModelStreamChunk(
                content="选择结果：```json\n",
                reasoning_content="分析候选",
                token_usage={"input_tokens": 3},
            ),
            ModelStreamChunk(
                content='{"id":101}\n```',
                token_usage={"total_tokens": 7},
            ),
        ]
    )
    service, _, _, _ = _service(model)

    events = list(service.generate(_data()))

    assert [event.kind for event in events] == ["chunk", "chunk", "completed"]
    assert events[-1].selected_datasource_id == 101
    assert events[-1].error is None
    assert events[-1].reasoning_content == "分析候选"
    assert events[-1].token_usage == {"input_tokens": 3, "total_tokens": 7}


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ('{"id":999}', "DATASOURCE_SELECTION_OUT_OF_SCOPE:999"),
        ('{"fail":"没有匹配的数据源"}', "没有匹配的数据源"),
        ("无法判断", "Cannot parse datasource from answer"),
    ],
)
def test_invalid_model_selection_returns_completed_error(
    content: str,
    error: str,
):
    service, _, _, _ = _service(
        FakeModelClient([ModelStreamChunk(content=content)])
    )

    completed = list(service.generate(_data()))[-1]

    assert completed.kind == "completed"
    assert completed.selected_datasource_id is None
    assert completed.error is not None
    assert error in completed.error


def test_bind_selection_updates_record_and_conversation_through_one_service():
    service, _, _, repository = _service()
    data = _data()
    event = DatasourceSelectionEvent(
        kind="completed",
        content='{"id":101}',
        selected_datasource_id=101,
        model_used=True,
    )

    record = service.bind_selection(
        data,
        event,
        record_engine_type="PostgreSQL 16",
        conversation_engine_type="PostgreSQL",
    )

    assert record.datasource == 101
    assert record.engine_type == "PostgreSQL 16"
    assert record.datasource_select_answer is not None
    assert orjson.loads(record.datasource_select_answer) == {
        "content": '{"id":101}'
    }
    assert repository.saved == 1
    assert repository.bindings == [(20, 101, "PostgreSQL")]


def test_empty_or_duplicate_candidates_fail_before_model_call():
    service, _, model, _ = _service()

    with pytest.raises(
        DatasourceSelectionError,
        match="No available datasource configuration found",
    ):
        service.prepare(_data(candidates=[]))
    with pytest.raises(
        DatasourceSelectionError,
        match="DATASOURCE_SELECTION_CANDIDATE_DUPLICATED:101",
    ):
        service.prepare(_data(candidates=[_candidates()[0], _candidates()[0]]))

    assert model.messages is None


def test_auto_selection_requires_exactly_one_candidate():
    service, _, _, _ = _service()

    with pytest.raises(
        DatasourceSelectionError,
        match="DATASOURCE_SELECTION_AUTO_SELECT_REQUIRES_ONE_CANDIDATE",
    ):
        service.prepare(_data(auto_select=True))
