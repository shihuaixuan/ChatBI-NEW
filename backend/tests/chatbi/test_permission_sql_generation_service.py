from __future__ import annotations

from collections.abc import Iterator

import pytest

from apps.chatbi.errors import PermissionSQLGenerationError
from apps.chatbi.models import (
    ModelMessage,
    ModelStreamChunk,
    PermissionSQLFilter,
    PermissionSQLGenerationData,
    SQLGenerationResult,
)
from apps.chatbi.services.generation import (
    PermissionSQLGenerationService,
)
from apps.conversation import ChatRecordService
from apps.conversation.models import ChatRecord


class FakePromptBuilder:
    def __init__(self) -> None:
        self.data: PermissionSQLGenerationData | None = None

    def build(
        self,
        data: PermissionSQLGenerationData,
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


class FakeChatRecordRepository:
    def __init__(self, record: ChatRecord) -> None:
        self.record = record
        self.saved = 0

    def get(self, record_id: int) -> ChatRecord | None:
        return self.record if self.record.id == record_id else None

    def create(self, data):
        raise AssertionError("权限 SQL 生成不应创建 ChatRecord")

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
        raise AssertionError("权限 SQL 生成不应提升推荐问题")

    def bind_conversation_datasource(
        self,
        chat_id: int,
        *,
        datasource_id: int,
        engine_type: str,
    ) -> None:
        raise AssertionError("权限 SQL 生成不应绑定数据源")


def _data(
    *,
    record_id: int = 10,
    sql: str = "SELECT * FROM orders",
    engine: str = "MySQL 8.0",
    filters: list[PermissionSQLFilter] | None = None,
) -> PermissionSQLGenerationData:
    return PermissionSQLGenerationData(
        record_id=record_id,
        sql=sql,
        filters=(
            [PermissionSQLFilter(table="orders", condition="region = '华东'")]
            if filters is None
            else filters
        ),
        language="简体中文",
        engine=engine,
        assistant_name="Numora",
    )


def _service(
    model: FakeModelClient | None = None,
) -> tuple[
    PermissionSQLGenerationService,
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
            question="查询订单",
            execution_type="graph",
            status="created",
            finish=False,
            sql="SELECT * FROM orders",
        )
    )
    service = PermissionSQLGenerationService(
        prompt_builder=prompt,
        model_client=model,
        chat_record_service=ChatRecordService(repository),
    )
    return service, prompt, model, repository


def test_prepare_builds_messages_from_permission_sql_data():
    service, prompt, _, _ = _service()
    data = _data()

    messages = service.prepare(data)

    assert prompt.data is data
    assert [message.role for message in messages] == ["system", "human"]


def test_generate_streams_chunks_parses_and_projects_final_sql_once():
    model = FakeModelClient(
        [
            ModelStreamChunk(
                content="```json\n",
                reasoning_content="分析权限条件",
                token_usage={"input_tokens": 6},
            ),
            ModelStreamChunk(
                content=(
                    '{"success":true,"sql":"SELECT * FROM orders '
                    "WHERE region = '华东'\"}\n```"
                ),
                reasoning_content="生成权限 SQL",
                token_usage={"total_tokens": 15},
            ),
        ]
    )
    service, _, _, repository = _service(model)

    events = list(service.generate(_data()))

    assert [event.kind for event in events] == ["chunk", "chunk", "completed"]
    assert events[-1].result == SQLGenerationResult(
        sql="SELECT * FROM orders WHERE region = '华东'"
    )
    assert events[-1].reasoning_content == "分析权限条件生成权限 SQL"
    assert events[-1].token_usage == {"input_tokens": 6, "total_tokens": 15}
    assert repository.record.sql == "SELECT * FROM orders WHERE region = '华东'"
    assert repository.saved == 1


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"success":false,"message":"权限条件无法应用"}', "权限条件无法应用"),
        ("无法生成 SQL", "SQL answer is not a valid json object"),
        ('{"success":true}', "Cannot parse sql from answer"),
        ('{"success":true,"sql":" "}', "SQL query is empty"),
    ],
)
def test_invalid_model_result_does_not_overwrite_existing_sql(
    content: str,
    message: str,
):
    service, _, _, repository = _service(
        FakeModelClient([ModelStreamChunk(content=content)])
    )

    completed = list(service.generate(_data()))[-1]

    assert completed.result is None
    assert completed.error is not None
    assert message in completed.error
    assert repository.record.sql == "SELECT * FROM orders"
    assert repository.saved == 0


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (_data(record_id=0), "PERMISSION_SQL_GENERATION_RECORD_ID_INVALID"),
        (_data(sql=" "), "PERMISSION_SQL_GENERATION_SQL_REQUIRED"),
        (_data(engine=" "), "PERMISSION_SQL_GENERATION_ENGINE_REQUIRED"),
        (
            _data(filters=[]),
            "PERMISSION_SQL_GENERATION_FILTERS_REQUIRED",
        ),
        (
            _data(
                filters=[PermissionSQLFilter(table=" ", condition="region = 1")]
            ),
            "PERMISSION_SQL_GENERATION_FILTER_INVALID",
        ),
        (
            _data(filters=[PermissionSQLFilter(table="orders", condition=" ")]),
            "PERMISSION_SQL_GENERATION_FILTER_INVALID",
        ),
    ],
)
def test_invalid_generation_data_fails_before_model_call(
    data: PermissionSQLGenerationData,
    error: str,
):
    service, _, model, _ = _service()

    with pytest.raises(PermissionSQLGenerationError, match=error):
        list(service.generate(data))

    assert model.messages is None


def test_explicit_empty_or_blank_messages_are_rejected():
    service, _, model, _ = _service()

    for messages in (
        [],
        [ModelMessage(role="human", content=" ")],
    ):
        with pytest.raises(
            PermissionSQLGenerationError,
            match="PERMISSION_SQL_GENERATION_PROMPT_INVALID",
        ):
            list(service.generate(_data(), messages))

    assert model.messages is None
