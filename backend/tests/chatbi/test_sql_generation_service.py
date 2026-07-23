from __future__ import annotations

from collections.abc import Iterator

import orjson
import pytest

from apps.chatbi.errors import SQLGenerationError
from apps.chatbi.models import (
    ChatRecord,
    ModelMessage,
    ModelStreamChunk,
    SQLGenerationData,
    SQLGenerationResult,
)
from apps.chatbi.services.conversation import ChatRecordService
from apps.chatbi.services.generation import (
    SQLGenerationService,
)


class FakePromptBuilder:
    def __init__(self) -> None:
        self.data: SQLGenerationData | None = None

    def build(
        self,
        data: SQLGenerationData,
    ) -> list[ModelMessage]:
        self.data = data
        return [
            ModelMessage(role="system", content=data.engine),
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
        raise AssertionError("SQL 生成不应创建 ChatRecord")

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
        raise AssertionError("SQL 生成不应提升推荐问题")

    def bind_conversation_datasource(
        self,
        chat_id: int,
        *,
        datasource_id: int,
        engine_type: str,
    ) -> None:
        raise AssertionError("SQL 生成不应绑定数据源")


def _data(
    *,
    record_id: int = 10,
    question: str = "查询本月销售额",
    database_type: str = "mysql",
    engine: str = "MySQL 8.0",
    schema: str = "orders(order_date, sales)",
    current_time: str = "2026-07-19 16:00:00",
) -> SQLGenerationData:
    return SQLGenerationData(
        record_id=record_id,
        question=question,
        database_type=database_type,
        engine=engine,
        schema=schema,
        sample_data='[{"sales":100}]',
        language="简体中文",
        assistant_name="Numora",
        current_time=current_time,
        rule="只统计已支付订单",
        custom_prompt="金额保留两位小数",
        terminologies="销售额：已支付订单金额",
        data_training="问题：昨日销售额；SQL：SELECT ...",
        history=[
            ModelMessage(role="human", content="上一轮问题"),
            ModelMessage(role="ai", content='{"success":true}'),
        ],
    )


def _service(
    model: FakeModelClient | None = None,
) -> tuple[
    SQLGenerationService,
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
    service = SQLGenerationService(
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


def test_generate_streams_chunks_parses_result_and_projects_answer_once():
    model = FakeModelClient(
        [
            ModelStreamChunk(
                content="结果：```json\n",
                reasoning_content="先确定时间范围",
                token_usage={"input_tokens": 8},
            ),
            ModelStreamChunk(
                content=(
                    '{"success":true,"sql":"SELECT SUM(sales) FROM orders",'
                    '"tables":["orders"],"chart-type":"number","brief":"本月销售额"}\n```'
                ),
                reasoning_content="再生成聚合 SQL",
                token_usage={"total_tokens": 20},
            ),
        ]
    )
    service, _, _, repository = _service(model)

    events = list(service.generate(_data()))

    assert [event.kind for event in events] == ["chunk", "chunk", "completed"]
    assert events[-1].result == SQLGenerationResult(
        sql="SELECT SUM(sales) FROM orders",
        tables=["orders"],
        chart_type="number",
        brief="本月销售额",
    )
    assert events[-1].reasoning_content == "先确定时间范围再生成聚合 SQL"
    assert events[-1].token_usage == {"input_tokens": 8, "total_tokens": 20}
    assert repository.saved == 1
    assert orjson.loads(repository.record.sql_answer or "") == {
        "content": events[-1].content
    }


def test_model_failure_preserves_message_and_projects_answer():
    content = '{"success":false,"message":"无法确定查询范围"}'
    service, _, _, repository = _service(
        FakeModelClient([ModelStreamChunk(content=content)])
    )

    completed = list(service.generate(_data()))[-1]

    assert completed.error == "无法确定查询范围"
    assert completed.result is None
    assert repository.saved == 1
    assert orjson.loads(repository.record.sql_answer or "") == {"content": content}


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("无法生成 SQL", "SQL answer is not a valid json object"),
        ('{"success":true}', "Cannot parse sql from answer"),
        ('{"success":true,"sql":123}', "Cannot parse sql from answer"),
        (
            '{"success":true,"sql":"SELECT 1","tables":"orders"}',
            "Cannot parse sql from answer",
        ),
        ('{"success":true,"sql":" "}', "SQL query is empty"),
    ],
)
def test_invalid_model_result_returns_completed_error(
    content: str,
    message: str,
):
    service, _, _, repository = _service(
        FakeModelClient([ModelStreamChunk(content=content)])
    )

    completed = list(service.generate(_data()))[-1]

    assert completed.error is not None
    assert message in completed.error
    assert completed.result is None
    assert repository.saved == 1
    assert repository.record.sql_answer is not None


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (_data(record_id=0), "SQL_GENERATION_RECORD_ID_INVALID"),
        (_data(question=" "), "SQL_GENERATION_QUESTION_REQUIRED"),
        (_data(database_type=" "), "SQL_GENERATION_ENGINE_REQUIRED"),
        (_data(engine=" "), "SQL_GENERATION_ENGINE_REQUIRED"),
        (_data(schema=" "), "SQL_GENERATION_SCHEMA_REQUIRED"),
        (_data(current_time=" "), "SQL_GENERATION_CURRENT_TIME_REQUIRED"),
    ],
)
def test_invalid_generation_data_fails_before_model_call(
    data: SQLGenerationData,
    error: str,
):
    service, _, model, _ = _service()

    with pytest.raises(SQLGenerationError, match=error):
        list(service.generate(data))

    assert model.messages is None


def test_explicit_empty_or_blank_messages_are_rejected():
    service, _, model, _ = _service()

    for messages in (
        [],
        [ModelMessage(role="human", content=" ")],
    ):
        with pytest.raises(
            SQLGenerationError,
            match="SQL_GENERATION_PROMPT_INVALID",
        ):
            list(service.generate(_data(), messages))

    assert model.messages is None
