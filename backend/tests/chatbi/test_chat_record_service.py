from datetime import datetime

import pytest

from apps.chatbi.models import (
    ChatRecord,
    ChatRecordExecutionType,
    ChatRecordResultProjection,
    ChatRecordStatus,
)
from apps.chatbi.services import (
    ChatRecordService,
    ChatRecordTransitionError,
    normalize_chat_record_status,
)


class FakeChatRecordRepository:
    def __init__(self, record: ChatRecord | None = None) -> None:
        self.record = record
        self.saved = 0

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


def test_failed_retry_clears_stale_terminal_snapshot():
    record = _record("failed")
    record.finish = True
    record.finish_time = datetime.now()
    record.error = "旧错误"
    record.sql_answer = "旧答案"
    record.sql = "select old"
    record.chart = "{}"
    record.data = "{}"
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
