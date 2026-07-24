from __future__ import annotations

from collections.abc import Iterator

import orjson
import pytest

from apps.chatbi.models import (
    ModelMessage,
    ModelStreamChunk,
    RecommendedQuestionGenerationData,
)
from apps.chatbi.services.generation import RecommendedQuestionService
from apps.conversation import ChatRecordService
from apps.conversation.models import ChatRecord


class FakeHistoryProvider:
    def __init__(self, questions: list[str]) -> None:
        self.questions = questions
        self.calls: list[tuple[int | None, int]] = []

    def list_recent(
        self,
        datasource_id: int | None,
        *,
        limit: int = 20,
    ) -> list[str]:
        self.calls.append((datasource_id, limit))
        return self.questions


class FakePromptBuilder:
    def __init__(self) -> None:
        self.old_questions: list[str] | None = None

    def build(
        self,
        data: RecommendedQuestionGenerationData,
        old_questions: list[str],
    ) -> list[ModelMessage]:
        self.old_questions = old_questions
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
        self.promotions: list[tuple[int, str, str]] = []

    def get(self, record_id: int) -> ChatRecord | None:
        return self.record if self.record.id == record_id else None

    def create(self, data):
        raise AssertionError("推荐问题生成不应创建 ChatRecord")

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
        self.promotions.append((chat_id, answer, questions))


def _data(**changes: object) -> RecommendedQuestionGenerationData:
    values: dict[str, object] = {
        "record_id": 10,
        "question": "查询本月销售额",
        "schema": "sales(amount, created_at)",
        "datasource_id": 30,
        "language": "简体中文",
        "assistant_name": "Numora",
        "articles_number": 2,
    }
    values.update(changes)
    return RecommendedQuestionGenerationData(**values)  # type: ignore[arg-type]


def _service(
    *,
    history: FakeHistoryProvider | None = None,
    prompt: FakePromptBuilder | None = None,
    model: FakeModelClient | None = None,
) -> tuple[
    RecommendedQuestionService,
    FakeHistoryProvider,
    FakePromptBuilder,
    FakeModelClient,
    FakeChatRecordRepository,
]:
    history = history or FakeHistoryProvider([])
    prompt = prompt or FakePromptBuilder()
    model = model or FakeModelClient([])
    repository = FakeChatRecordRepository(
        ChatRecord(
            id=10,
            chat_id=20,
            create_by=40,
            question="查询本月销售额",
            datasource=30,
            execution_type="graph",
            status="succeeded",
            finish=True,
        )
    )
    return (
        RecommendedQuestionService(
            history_provider=history,
            prompt_builder=prompt,
            model_client=model,
            chat_record_service=ChatRecordService(repository),
        ),
        history,
        prompt,
        model,
        repository,
    )


def test_prepare_trims_history_and_builds_stable_messages():
    service, history, prompt, _, _ = _service(
        history=FakeHistoryProvider(["  问题一  ", "", "   ", "问题二"])
    )

    messages = service.prepare(_data())

    assert history.calls == [(30, 20)]
    assert prompt.old_questions == ["问题一", "问题二"]
    assert [message.role for message in messages] == ["system", "human"]


def test_generate_streams_chunks_normalizes_json_and_projects_record():
    model = FakeModelClient(
        [
            ModelStreamChunk(
                content='```json\n[" 问题一 ", 42,',
                reasoning_content="思考一",
                token_usage={"input_tokens": 3},
            ),
            ModelStreamChunk(
                content='"问题二", "", "问题三"]\n```',
                reasoning_content="思考二",
                token_usage={"total_tokens": 9},
            ),
        ]
    )
    service, _, _, _, repository = _service(model=model)

    events = list(service.generate(_data()))

    assert [event.kind for event in events] == ["chunk", "chunk", "completed"]
    assert events[0].content == '```json\n[" 问题一 ", 42,'
    assert events[1].reasoning_content == "思考二"
    assert events[-1].reasoning_content == "思考一思考二"
    assert events[-1].recommended_question == '["问题一","问题二"]'
    assert events[-1].token_usage == {"input_tokens": 3, "total_tokens": 9}
    assert repository.record.recommended_question == '["问题一","问题二"]'
    answer = repository.record.recommended_question_answer
    assert answer is not None
    assert orjson.loads(answer) == {
        "content": '```json\n[" 问题一 ", 42,"问题二", "", "问题三"]\n```'
    }
    assert repository.saved == 1


def test_generate_saves_empty_array_when_model_has_no_valid_json_array():
    service, _, _, _, repository = _service(
        model=FakeModelClient(
            [ModelStreamChunk(content="没有可用的推荐问题")]
        )
    )

    completed = list(service.generate(_data()))[-1]

    assert completed.recommended_question == "[]"
    assert repository.record.recommended_question == "[]"


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"record_id": 0}, "RECOMMENDED_QUESTION_RECORD_ID_INVALID"),
        ({"question": "  "}, "RECOMMENDED_QUESTION_REQUIRED"),
        ({"articles_number": 0}, "RECOMMENDED_QUESTION_COUNT_INVALID"),
    ],
)
def test_invalid_generation_data_fails_before_model_call(
    changes: dict[str, object],
    error: str,
):
    service, _, _, model, _ = _service()

    with pytest.raises(ValueError, match=error):
        list(service.generate(_data(**changes)))

    assert model.messages is None


def test_explicit_empty_messages_are_rejected():
    service, _, _, _, _ = _service()

    with pytest.raises(ValueError, match="RECOMMENDED_QUESTION_PROMPT_INVALID"):
        list(service.generate(_data(), []))
