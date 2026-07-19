from __future__ import annotations

from typing import Any, cast

import orjson
import pytest

from apps.chatbi.models import ChatRecord, QueryResultProjectionData
from apps.chatbi.services import (
    ChatRecordService,
    QueryResultProjectionError,
    QueryResultProjectionService,
)


class FakeChatRecordRepository:
    def __init__(self) -> None:
        self.record = ChatRecord(
            id=10,
            chat_id=20,
            create_by=30,
            question="查询销售额",
            execution_type="graph",
            status="created",
            finish=False,
        )
        self.saved = 0

    def get(self, record_id: int) -> ChatRecord | None:
        return self.record if self.record.id == record_id else None

    def create(self, data):
        raise AssertionError("查询结果投影不应创建 ChatRecord")

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
        raise AssertionError("查询结果投影不应提升推荐问题")

    def bind_conversation_datasource(
        self,
        chat_id: int,
        *,
        datasource_id: int,
        engine_type: str,
    ) -> None:
        raise AssertionError("查询结果投影不应绑定会话数据源")


def _service() -> tuple[QueryResultProjectionService, FakeChatRecordRepository]:
    repository = FakeChatRecordRepository()
    return (
        QueryResultProjectionService(ChatRecordService(repository)),
        repository,
    )


def _data(**overrides: Any) -> QueryResultProjectionData:
    values: dict[str, Any] = {
        "record_id": 10,
        "datasource_id": 8,
        "fields": ["order_id", "amount"],
        "rows": [{"order_id": 1, "amount": 10}],
        "execution_metadata": {"sql": "encoded-sql"},
        "enable_row_limit": True,
        "row_limit": 1000,
    }
    values.update(overrides)
    return QueryResultProjectionData(**values)


def test_project_normalizes_nested_numbers_bytes_and_qualified_keys():
    service, repository = _service()

    result = service.project(
        _data(
            fields=["orders.order_id", "amount", "payload"],
            rows=[
                {
                    "orders.order_id": 1000000000000001,
                    "amount": 20,
                    "orders.amount": 10,
                    "payload": {
                        "large_float": 10000000000.5,
                        "items": [1000000000000002, {"tiny": 0.0000001}],
                        "raw": b"abc",
                    },
                }
            ],
            execution_metadata={"sql": "encoded-sql", "raw": b"meta"},
        )
    )

    assert result == {
        "sql": "encoded-sql",
        "raw": "bWV0YQ==",
        "fields": ["orders.order_id", "amount", "payload"],
        "data": [
            {
                "orders.order_id": "1000000000000001",
                "order_id": "1000000000000001",
                "amount": 20,
                "orders.amount": 10,
                "payload": {
                    "large_float": "10000000000.5",
                    "items": ["1000000000000002", {"tiny": "0.0000001"}],
                    "raw": "YWJj",
                },
            }
        ],
        "datasource": 8,
    }
    assert repository.saved == 1
    assert orjson.loads(repository.record.data or "") == result


def test_project_limits_rows_and_projects_only_once():
    service, repository = _service()
    rows = [{"id": index} for index in range(1001)]

    result = service.project(_data(fields=["id"], rows=rows))

    assert len(result["data"]) == 1000
    assert result["limit"] == 1000
    assert result["datasource"] == 8
    assert repository.saved == 1


def test_project_can_disable_row_limit():
    service, repository = _service()
    rows = [{"id": index} for index in range(1001)]

    result = service.project(
        _data(fields=["id"], rows=rows, enable_row_limit=False)
    )

    assert len(result["data"]) == 1001
    assert "limit" not in result
    assert repository.saved == 1


def test_project_keeps_empty_result_without_datasource():
    service, repository = _service()

    result = service.project(
        _data(fields=[], rows=[], execution_metadata={"sql": "encoded-sql"})
    )

    assert result == {"sql": "encoded-sql", "fields": [], "data": []}
    assert repository.saved == 1


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"record_id": 0}, "QUERY_RESULT_PROJECTION_RECORD_ID_INVALID"),
        ({"datasource_id": 0}, "QUERY_RESULT_PROJECTION_DATASOURCE_ID_INVALID"),
        (
            {"fields": cast(Any, ["valid", 1])},
            "QUERY_RESULT_PROJECTION_FIELDS_INVALID",
        ),
        (
            {"rows": cast(Any, [{"id": 1}, "invalid"])},
            "QUERY_RESULT_PROJECTION_ROWS_INVALID",
        ),
        (
            {"rows": cast(Any, [{"nested": {1: "invalid"}}])},
            "QUERY_RESULT_PROJECTION_ROWS_INVALID",
        ),
        (
            {"execution_metadata": cast(Any, {1: "invalid"})},
            "QUERY_RESULT_PROJECTION_METADATA_INVALID",
        ),
        (
            {"enable_row_limit": cast(Any, 1)},
            "QUERY_RESULT_PROJECTION_ROW_LIMIT_FLAG_INVALID",
        ),
        ({"row_limit": 0}, "QUERY_RESULT_PROJECTION_ROW_LIMIT_INVALID"),
    ],
)
def test_project_rejects_invalid_input(overrides: dict[str, Any], error: str):
    service, repository = _service()

    with pytest.raises(QueryResultProjectionError, match=error):
        service.project(_data(**overrides))

    assert repository.saved == 0


def test_project_reports_unsupported_serialization_value():
    service, repository = _service()

    with pytest.raises(
        QueryResultProjectionError,
        match="QUERY_RESULT_PROJECTION_SERIALIZATION_FAILED",
    ):
        service.project(_data(rows=[{"value": object()}]))

    assert repository.saved == 0
