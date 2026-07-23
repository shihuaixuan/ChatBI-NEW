from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import orjson
import pytest

from apps.chatbi.models import (
    AnalysisPredictionGenerationData,
    ChatRecord,
    ChatRecordAuxiliaryType,
    ModelMessage,
    ModelStreamChunk,
)
from apps.chatbi.services.conversation import ChatRecordService
from apps.chatbi.services.generation import AnalysisPredictionService


class FakePromptBuilder:
    def __init__(self) -> None:
        self.data: AnalysisPredictionGenerationData | None = None

    def build(
        self,
        data: AnalysisPredictionGenerationData,
    ) -> list[ModelMessage]:
        self.data = data
        return [
            ModelMessage(role="system", content=data.language),
            ModelMessage(role="human", content=data.data),
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

    def get(self, record_id: int) -> ChatRecord | None:
        return self.record if self.record.id == record_id else None

    def create(self, data):
        raise AssertionError("分析预测生成不应创建 ChatRecord")

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
        raise AssertionError("分析预测生成不应提升推荐问题")


def _data(
    generation_type: ChatRecordAuxiliaryType = ChatRecordAuxiliaryType.ANALYSIS,
    *,
    record_id: int = 10,
) -> AnalysisPredictionGenerationData:
    return AnalysisPredictionGenerationData(
        record_id=record_id,
        generation_type=generation_type,
        fields='[{"name":"月份"},{"name":"销售额"}]',
        data='[{"月份":"1月","销售额":100}]',
        language="简体中文",
        assistant_name="Numora",
        terminologies="销售额：已支付订单金额",
    )


def _service(
    model: FakeModelClient | None = None,
) -> tuple[
    AnalysisPredictionService,
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
            question="销售趋势",
            execution_type="graph",
            status="created",
            finish=False,
        )
    )
    service = AnalysisPredictionService(
        prompt_builder=prompt,
        model_client=model,
        chat_record_service=ChatRecordService(repository),
    )
    return service, prompt, model, repository


def test_prepare_builds_stable_messages_from_generation_data():
    service, prompt, _, _ = _service()
    data = _data()

    messages = service.prepare(data)

    assert prompt.data is data
    assert [message.role for message in messages] == ["system", "human"]


@pytest.mark.parametrize(
    ("generation_type", "field_name"),
    [
        (ChatRecordAuxiliaryType.ANALYSIS, "analysis"),
        (ChatRecordAuxiliaryType.PREDICT, "predict"),
    ],
)
def test_generate_streams_chunks_and_projects_matching_auxiliary_field(
    generation_type: ChatRecordAuxiliaryType,
    field_name: str,
):
    model = FakeModelClient(
        [
            ModelStreamChunk(
                content="第一段",
                reasoning_content="思考一",
                token_usage={"input_tokens": 4},
            ),
            ModelStreamChunk(
                content="第二段",
                reasoning_content="思考二",
                token_usage={"total_tokens": 10},
            ),
        ]
    )
    service, _, _, repository = _service(model)

    events = list(service.generate(_data(generation_type)))

    assert [event.kind for event in events] == ["chunk", "chunk", "completed"]
    assert events[-1].content == "第一段第二段"
    assert events[-1].reasoning_content == "思考一思考二"
    assert events[-1].token_usage == {"input_tokens": 4, "total_tokens": 10}
    projected = getattr(repository.record, field_name)
    assert projected is not None
    assert orjson.loads(projected) == {"content": "第一段第二段"}
    other_field = "predict" if field_name == "analysis" else "analysis"
    assert getattr(repository.record, other_field) is None
    assert repository.saved == 1


def test_invalid_record_id_fails_before_model_call():
    service, _, model, _ = _service()

    with pytest.raises(ValueError, match="ANALYSIS_PREDICTION_RECORD_ID_INVALID"):
        list(service.generate(_data(record_id=0)))

    assert model.messages is None


def test_invalid_generation_type_fails_before_model_call():
    service, _, model, _ = _service()
    invalid_type = cast(ChatRecordAuxiliaryType, "summary")

    with pytest.raises(ValueError, match="ANALYSIS_PREDICTION_TYPE_INVALID"):
        list(service.generate(_data(invalid_type)))

    assert model.messages is None


def test_explicit_empty_messages_are_rejected():
    service, _, _, _ = _service()

    with pytest.raises(ValueError, match="ANALYSIS_PREDICTION_PROMPT_INVALID"):
        list(service.generate(_data(), []))
