from __future__ import annotations

from collections.abc import Iterator

import orjson
import pytest

from apps.chatbi.errors import ChartGenerationError
from apps.chatbi.models import (
    ChartGenerationData,
    ModelMessage,
    ModelStreamChunk,
)
from apps.chatbi.services.generation import (
    ChartGenerationService,
)
from apps.conversation import ChatRecordService
from apps.conversation.models import ChatRecord


class FakePromptBuilder:
    def __init__(self) -> None:
        self.data: ChartGenerationData | None = None

    def build(
        self,
        data: ChartGenerationData,
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

    def get(self, record_id: int) -> ChatRecord | None:
        return self.record if self.record.id == record_id else None

    def create(self, data):
        raise AssertionError("图表生成不应创建 ChatRecord")

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
        raise AssertionError("图表生成不应提升推荐问题")

    def bind_conversation_datasource(
        self,
        chat_id: int,
        *,
        datasource_id: int,
        engine_type: str,
    ) -> None:
        raise AssertionError("图表生成不应绑定数据源")


def _data(
    *,
    record_id: int = 10,
    question: str = "查询销售趋势",
    sql: str = "SELECT Month, Sales FROM orders",
) -> ChartGenerationData:
    return ChartGenerationData(
        record_id=record_id,
        question=question,
        sql=sql,
        schema="orders(Month, Sales, Region)",
        chart_type="line",
        language="简体中文",
        assistant_name="Numora",
        rule="销售额保留两位小数",
        history=[
            ModelMessage(role="human", content="上一轮问题"),
            ModelMessage(role="ai", content='{"type":"table"}'),
        ],
    )


def _service(
    model: FakeModelClient | None = None,
) -> tuple[
    ChartGenerationService,
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
            question="查询销售趋势",
            execution_type="graph",
            status="created",
            finish=False,
        )
    )
    service = ChartGenerationService(
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


def test_generate_streams_chunks_and_projects_answer_and_chart_once():
    model = FakeModelClient(
        [
            ModelStreamChunk(
                content="图表配置：```json\n",
                reasoning_content="先分析字段",
                token_usage={"input_tokens": 5},
            ),
            ModelStreamChunk(
                content=(
                    '{"type":"line","axis":{"x":{"value":"Month"},'
                    '"y":[{"value":"Sales"}],"series":{"value":"Region"},'
                    '"multi-quota":{"value":["Sales","PROFIT"]}}}\n```'
                ),
                reasoning_content="再选择折线图",
                token_usage={"total_tokens": 12},
            ),
        ]
    )
    service, _, _, repository = _service(model)

    events = list(service.generate(_data()))

    assert [event.kind for event in events] == ["chunk", "chunk", "completed"]
    assert events[-1].reasoning_content == "先分析字段再选择折线图"
    assert events[-1].token_usage == {"input_tokens": 5, "total_tokens": 12}
    assert events[-1].chart == {
        "type": "line",
        "axis": {
            "x": {"value": "month"},
            "y": [{"value": "sales"}],
            "series": {"value": "region"},
            "multi-quota": {"value": ["sales", "profit"]},
        },
    }
    assert repository.saved == 1
    assert orjson.loads(repository.record.chart_answer or "") == {
        "content": events[-1].content
    }
    assert orjson.loads(repository.record.chart or "") == events[-1].chart


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            '{"type":"table","columns":[{"value":"Order_ID"}]}',
            {"type": "table", "columns": [{"value": "order_id"}]},
        ),
        (
            '{"type":"bar","axis":{"y":{"value":"TOTAL"}}}',
            {"type": "bar", "axis": {"y": {"value": "total"}}},
        ),
        (
            '{"type":"bar","axis":{"multi-quota":{"value":"TOTAL"}}}',
            {
                "type": "bar",
                "axis": {"multi-quota": {"value": "total"}},
            },
        ),
    ],
)
def test_generate_normalizes_supported_chart_value_aliases(
    content: str,
    expected: dict,
):
    service, _, _, _ = _service(
        FakeModelClient([ModelStreamChunk(content=content)])
    )

    completed = list(service.generate(_data()))[-1]

    assert completed.error is None
    assert completed.chart == expected


def test_model_error_preserves_reason_and_only_projects_chart_answer():
    content = '{"type":"error","reason":"当前结果不适合生成图表"}'
    service, _, _, repository = _service(
        FakeModelClient([ModelStreamChunk(content=content)])
    )

    completed = list(service.generate(_data()))[-1]

    assert completed.error == "当前结果不适合生成图表"
    assert completed.chart is None
    assert repository.saved == 1
    assert orjson.loads(repository.record.chart_answer or "") == {
        "content": content
    }
    assert repository.record.chart is None


def test_invalid_json_returns_legacy_error_and_only_projects_chart_answer():
    content = "无法生成图表"
    service, _, _, repository = _service(
        FakeModelClient([ModelStreamChunk(content=content)])
    )

    completed = list(service.generate(_data()))[-1]

    assert completed.error is not None
    assert orjson.loads(completed.error) == {
        "message": "Cannot parse chart config from answer",
        "traceback": "Cannot parse chart config from answer:\n" + content,
    }
    assert repository.saved == 1
    assert repository.record.chart_answer is not None
    assert repository.record.chart is None


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (_data(record_id=0), "CHART_GENERATION_RECORD_ID_INVALID"),
        (_data(question=" "), "CHART_GENERATION_QUESTION_REQUIRED"),
        (_data(sql=" "), "CHART_GENERATION_SQL_REQUIRED"),
    ],
)
def test_invalid_generation_data_fails_before_model_call(
    data: ChartGenerationData,
    error: str,
):
    service, _, model, _ = _service()

    with pytest.raises(ChartGenerationError, match=error):
        list(service.generate(data))

    assert model.messages is None


def test_explicit_empty_or_blank_messages_are_rejected():
    service, _, model, _ = _service()

    for messages in (
        [],
        [ModelMessage(role="human", content=" ")],
    ):
        with pytest.raises(
            ChartGenerationError,
            match="CHART_GENERATION_PROMPT_INVALID",
        ):
            list(service.generate(_data(), messages))

    assert model.messages is None
