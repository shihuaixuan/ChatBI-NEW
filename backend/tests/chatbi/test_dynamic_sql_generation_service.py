from __future__ import annotations

from collections.abc import Iterator

import pytest

from apps.chatbi.models import (
    DynamicSQLGenerationData,
    DynamicSQLSubqueryMapping,
    ModelMessage,
    ModelStreamChunk,
    SQLGenerationResult,
)
from apps.chatbi.services.generation import (
    DynamicSQLGenerationError,
    DynamicSQLGenerationService,
)


class FakePromptBuilder:
    def __init__(self) -> None:
        self.data: DynamicSQLGenerationData | None = None

    def build(
        self,
        data: DynamicSQLGenerationData,
    ) -> list[ModelMessage]:
        self.data = data
        return [
            ModelMessage(role="system", content=data.engine),
            ModelMessage(role="human", content=data.sql),
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


def _data(
    *,
    sql: str = "SELECT * FROM orders",
    engine: str = "MySQL 8.0",
    subqueries: list[DynamicSQLSubqueryMapping] | None = None,
) -> DynamicSQLGenerationData:
    return DynamicSQLGenerationData(
        sql=sql,
        subqueries=(
            [
                DynamicSQLSubqueryMapping(
                    table="orders",
                    query="sqlbot_dynamic_subsql_orders",
                )
            ]
            if subqueries is None
            else subqueries
        ),
        language="简体中文",
        engine=engine,
        assistant_name="Numora",
    )


def _service(
    model: FakeModelClient | None = None,
) -> tuple[DynamicSQLGenerationService, FakePromptBuilder, FakeModelClient]:
    prompt = FakePromptBuilder()
    model = model or FakeModelClient([])
    service = DynamicSQLGenerationService(
        prompt_builder=prompt,
        model_client=model,
    )
    return service, prompt, model


def test_prepare_builds_messages_from_dynamic_sql_data():
    service, prompt, _ = _service()
    data = _data()

    messages = service.prepare(data)

    assert prompt.data is data
    assert [message.role for message in messages] == ["system", "human"]


def test_generate_streams_chunks_and_reuses_sql_result_parser():
    model = FakeModelClient(
        [
            ModelStreamChunk(
                content="```json\n",
                reasoning_content="识别需要替换的表",
                token_usage={"input_tokens": 5},
            ),
            ModelStreamChunk(
                content=(
                    '{"success":true,"sql":"SELECT * FROM '
                    'sqlbot_dynamic_subsql_orders"}\n```'
                ),
                reasoning_content="生成占位 SQL",
                token_usage={"total_tokens": 13},
            ),
        ]
    )
    service, _, _ = _service(model)

    events = list(service.generate(_data()))

    assert [event.kind for event in events] == ["chunk", "chunk", "completed"]
    assert events[-1].result == SQLGenerationResult(
        sql="SELECT * FROM sqlbot_dynamic_subsql_orders"
    )
    assert events[-1].reasoning_content == "识别需要替换的表生成占位 SQL"
    assert events[-1].token_usage == {"input_tokens": 5, "total_tokens": 13}


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"success":false,"message":"子查询映射失败"}', "子查询映射失败"),
        ("无法生成 SQL", "SQL answer is not a valid json object"),
        ('{"success":true}', "Cannot parse sql from answer"),
        ('{"success":true,"sql":" "}', "SQL query is empty"),
    ],
)
def test_invalid_model_result_returns_completed_error(
    content: str,
    message: str,
):
    service, _, _ = _service(
        FakeModelClient([ModelStreamChunk(content=content)])
    )

    completed = list(service.generate(_data()))[-1]

    assert completed.result is None
    assert completed.error is not None
    assert message in completed.error


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (_data(sql=" "), "DYNAMIC_SQL_GENERATION_SQL_REQUIRED"),
        (_data(engine=" "), "DYNAMIC_SQL_GENERATION_ENGINE_REQUIRED"),
        (
            _data(subqueries=[]),
            "DYNAMIC_SQL_GENERATION_SUBQUERIES_REQUIRED",
        ),
        (
            _data(
                subqueries=[
                    DynamicSQLSubqueryMapping(table=" ", query="placeholder")
                ]
            ),
            "DYNAMIC_SQL_GENERATION_MAPPING_INVALID",
        ),
        (
            _data(
                subqueries=[DynamicSQLSubqueryMapping(table="orders", query=" ")]
            ),
            "DYNAMIC_SQL_GENERATION_MAPPING_INVALID",
        ),
    ],
)
def test_invalid_generation_data_fails_before_model_call(
    data: DynamicSQLGenerationData,
    error: str,
):
    service, _, model = _service()

    with pytest.raises(DynamicSQLGenerationError, match=error):
        list(service.generate(data))

    assert model.messages is None


def test_explicit_empty_or_blank_messages_are_rejected():
    service, _, model = _service()

    for messages in (
        [],
        [ModelMessage(role="human", content=" ")],
    ):
        with pytest.raises(
            DynamicSQLGenerationError,
            match="DYNAMIC_SQL_GENERATION_PROMPT_INVALID",
        ):
            list(service.generate(_data(), messages))

    assert model.messages is None
