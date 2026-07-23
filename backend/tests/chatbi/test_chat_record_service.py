from datetime import datetime

import orjson
import pytest

from apps.chatbi.errors import (
    ChatRecordResultTooLargeError,
    ChatRecordTransitionError,
)
from apps.chatbi.models import (
    ChatRecord,
    ChatRecordAuxiliaryProjection,
    ChatRecordAuxiliaryType,
    ChatRecordExecutionType,
    ChatRecordResultLimits,
    ChatRecordResultProjection,
    ChatRecordStatus,
)
from apps.chatbi.services.conversation import (
    ChatRecordService,
    normalize_chat_record_status,
)


class FakeChatRecordRepository:
    def __init__(self, record: ChatRecord | None = None) -> None:
        self.record = record
        self.saved = 0
        self.promotions = []

    def get(self, record_id: int) -> ChatRecord | None:
        if self.record is not None and self.record.id == record_id:
            return self.record
        return None

    def create(self, data):
        self.record = ChatRecord(
            id=10,
            chat_id=data.chat_id,
            create_by=data.user_id,
            question=data.question,
            dataset_id=data.dataset_id,
            datasource=data.datasource_id,
            engine_type=data.engine_type,
            execution_type=data.execution_type.value,
            status="created",
            finish=False,
        )
        return self.record

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


def _record(status: str | None = "created") -> ChatRecord:
    return ChatRecord(
        id=10,
        chat_id=20,
        create_by=30,
        question="销售额",
        execution_type="agent",
        status=status,
        finish=False,
    )


def test_status_aliases_normalize_agent_and_graph_runtime_values():
    assert normalize_chat_record_status("finished") is ChatRecordStatus.SUCCEEDED
    assert normalize_chat_record_status("succeeded") is ChatRecordStatus.SUCCEEDED
    assert normalize_chat_record_status("waiting_input") is ChatRecordStatus.WAITING_USER
    assert normalize_chat_record_status("waiting_user") is ChatRecordStatus.WAITING_USER


def test_terminal_statuses_share_finish_invariant():
    for status, error in (
        ("finished", None),
        ("failed", "执行失败"),
        ("cancelled", None),
    ):
        record = _record("running")
        service = ChatRecordService(FakeChatRecordRepository(record))

        service.transition(record, status, error=error)

        assert record.finish is True
        assert record.finish_time is not None
        assert record.status in {"succeeded", "failed", "cancelled"}


def test_success_projects_result_and_clears_error():
    record = _record("running")
    record.error = "旧错误"
    service = ChatRecordService(FakeChatRecordRepository(record))

    service.transition(
        record,
        ChatRecordStatus.SUCCEEDED,
        execution_type=ChatRecordExecutionType.AGENT,
        result=ChatRecordResultProjection(
            answer="销售额为 100 元",
            sql="select 100 as sales",
            chart='{"type":"indicator"}',
            data='{"fields":["sales"],"data":[{"sales":100}]}',
        ),
    )

    assert record.status == "succeeded"
    assert record.sql_answer == "销售额为 100 元"
    assert record.sql == "select 100 as sales"
    assert record.error is None


def test_large_data_is_saved_as_explicit_bounded_summary():
    record = _record("running")
    service = ChatRecordService(
        FakeChatRecordRepository(record),
        result_limits=ChatRecordResultLimits(max_data_bytes=220),
    )
    source = {
        "fields": ["name", "amount"],
        "data": [
            {"name": f"门店-{index}-" + "长名称" * 5, "amount": index}
            for index in range(10)
        ],
        "row_count": 10,
        "artifact_ref": {"artifact_id": "artifact-1"},
    }

    service.transition(
        record,
        ChatRecordStatus.SUCCEEDED,
        result=ChatRecordResultProjection(data=orjson.dumps(source).decode()),
    )

    stored = orjson.loads(record.data)
    assert len(record.data.encode("utf-8")) <= 220
    assert stored["result_truncated"] is True
    assert stored["row_count"] == 10
    assert stored["stored_row_count"] < 10
    assert stored["artifact_ref"] == {"artifact_id": "artifact-1"}


def test_oversized_text_rejects_transition_without_partial_mutation():
    record = _record("running")
    service = ChatRecordService(
        FakeChatRecordRepository(record),
        result_limits=ChatRecordResultLimits(max_answer_chars=5),
    )

    with pytest.raises(
        ChatRecordResultTooLargeError,
        match="CHAT_RECORD_ANSWER_TOO_LARGE",
    ):
        service.transition(
            record,
            ChatRecordStatus.SUCCEEDED,
            result=ChatRecordResultProjection(answer="超过五个字符的回答"),
        )

    assert record.status == "running"
    assert record.finish is False
    assert record.sql_answer is None


def test_oversized_non_json_data_returns_clear_error():
    record = _record("running")
    service = ChatRecordService(
        FakeChatRecordRepository(record),
        result_limits=ChatRecordResultLimits(max_data_bytes=10),
    )

    with pytest.raises(
        ChatRecordResultTooLargeError,
        match="CHAT_RECORD_DATA_TOO_LARGE_INVALID_JSON",
    ):
        service.transition(
            record,
            ChatRecordStatus.SUCCEEDED,
            result=ChatRecordResultProjection(data="不是合法且足够长的 JSON"),
        )


def test_draft_result_projection_does_not_change_record_status():
    record = _record("running")
    service = ChatRecordService(FakeChatRecordRepository(record))

    service.project_result(
        record,
        ChatRecordResultProjection(
            answer="SQL 生成中",
            sql="select amount from orders",
            chart='{"type":"table"}',
            data='{"fields":["amount"],"data":[{"amount":10}]}',
        ),
    )

    assert record.status == "running"
    assert record.finish is False
    assert record.sql_answer == "SQL 生成中"
    assert record.sql == "select amount from orders"
    assert record.chart == '{"type":"table"}'


def test_draft_result_projection_rejects_terminal_record():
    record = _record("succeeded")
    record.finish = True
    service = ChatRecordService(FakeChatRecordRepository(record))

    with pytest.raises(
        ChatRecordTransitionError,
        match="CHAT_RECORD_RESULT_UPDATE_TERMINAL",
    ):
        service.project_result(
            record,
            ChatRecordResultProjection(sql="select changed"),
        )

    assert record.sql is None


def test_draft_result_projection_applies_same_size_limits_as_final_result():
    record = _record("running")
    service = ChatRecordService(
        FakeChatRecordRepository(record),
        result_limits=ChatRecordResultLimits(max_sql_chars=5),
    )

    with pytest.raises(
        ChatRecordResultTooLargeError,
        match="CHAT_RECORD_SQL_TOO_LARGE",
    ):
        service.project_result(
            record,
            ChatRecordResultProjection(sql="select amount from orders"),
        )

    assert record.sql is None


def test_auxiliary_projection_allows_terminal_record_and_keeps_status():
    record = _record("succeeded")
    record.finish = True
    service = ChatRecordService(FakeChatRecordRepository(record))

    service.project_auxiliary(
        record,
        ChatRecordAuxiliaryProjection(
            analysis='{"content":"分析结果"}',
            predict='{"content":"预测结果"}',
            predict_data='[{"month":"2026-08","value":10}]',
        ),
    )

    assert record.status == "succeeded"
    assert record.finish is True
    assert record.analysis == '{"content":"分析结果"}'
    assert record.predict == '{"content":"预测结果"}'
    assert record.predict_data == '[{"month":"2026-08","value":10}]'


def test_auxiliary_projection_rejects_oversized_predict_data_without_mutation():
    record = _record("running")
    service = ChatRecordService(
        FakeChatRecordRepository(record),
        result_limits=ChatRecordResultLimits(max_data_bytes=10),
    )

    with pytest.raises(
        ChatRecordResultTooLargeError,
        match="CHAT_RECORD_PREDICT_DATA_TOO_LARGE",
    ):
        service.project_auxiliary(
            record,
            ChatRecordAuxiliaryProjection(predict_data='[{"value":12345}]'),
        )

    assert record.predict_data is None


def test_auxiliary_datasource_binding_requires_engine_type():
    record = _record("created")
    service = ChatRecordService(FakeChatRecordRepository(record))

    with pytest.raises(
        ValueError,
        match="CHAT_RECORD_DATASOURCE_BINDING_INVALID",
    ):
        service.project_auxiliary(
            record,
            ChatRecordAuxiliaryProjection(datasource_id=100),
        )

    assert record.datasource is None


def test_extended_recommendation_is_promoted_to_conversation():
    record = _record("succeeded")
    record.finish = True
    repository = FakeChatRecordRepository(record)
    service = ChatRecordService(repository)

    service.project_recommendation_by_id(
        record.id or 0,
        answer='{"content":"[\\"问题一\\"]"}',
        questions='["问题一"]',
        articles_number=5,
    )

    assert record.recommended_question == '["问题一"]'
    assert repository.promotions == [
        (20, '{"content":"[\\"问题一\\"]"}', '["问题一"]')
    ]


def test_create_auxiliary_record_copies_source_snapshot_and_relation():
    base_record = _record("succeeded")
    base_record.execution_type = "agent"
    base_record.chart = '{"type":"line"}'
    base_record.data = '{"fields":["month"],"data":[{"month":"2026-07"}]}'
    repository = FakeChatRecordRepository(base_record)
    service = ChatRecordService(repository)

    created = service.create_auxiliary(
        base_record,
        ChatRecordAuxiliaryType.ANALYSIS,
    )

    assert created.id == 10
    assert created.execution_type == "agent"
    assert created.analysis_record_id == base_record.id
    assert created.predict_record_id is None
    assert created.chart == base_record.chart
    assert created.data == base_record.data


def test_failed_retry_clears_stale_terminal_snapshot():
    record = _record("failed")
    record.finish = True
    record.finish_time = datetime.now()
    record.error = "旧错误"
    record.sql_answer = "旧答案"
    record.sql = "select old"
    record.chart = "{}"
    record.data = "{}"
    record.analysis = "旧分析"
    record.predict_data = "旧预测数据"
    service = ChatRecordService(FakeChatRecordRepository(record))

    service.transition(record, ChatRecordStatus.RUNNING)

    assert record.status == "running"
    assert record.finish is False
    assert record.finish_time is None
    assert record.error is None
    assert record.sql_answer is None
    assert record.sql is None
    assert record.chart is None
    assert record.data is None
    assert record.analysis is None
    assert record.predict_data is None


def test_terminal_record_rejects_illegal_reopen():
    record = _record("succeeded")
    record.finish = True
    service = ChatRecordService(FakeChatRecordRepository(record))

    with pytest.raises(
        ChatRecordTransitionError,
        match="succeeded->running",
    ):
        service.transition(record, ChatRecordStatus.RUNNING)


def test_legacy_unknown_status_can_enter_canonical_state():
    record = _record("legacy-finished")
    record.finish = True
    record.sql_answer = "旧答案"
    service = ChatRecordService(FakeChatRecordRepository(record))

    service.transition(record, ChatRecordStatus.CREATED)

    assert record.status == "created"
    assert record.finish is False
    assert record.sql_answer is None


def test_failed_status_requires_explicit_error():
    record = _record("running")
    service = ChatRecordService(FakeChatRecordRepository(record))

    with pytest.raises(
        ChatRecordTransitionError,
        match="FAILED_ERROR_REQUIRED",
    ):
        service.transition(record, ChatRecordStatus.FAILED)
