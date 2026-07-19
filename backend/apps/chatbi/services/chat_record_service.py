from datetime import datetime
from typing import Any

import orjson

from apps.chatbi.models import (
    ChatRecord,
    ChatRecordCreateData,
    ChatRecordExecutionType,
    ChatRecordResultLimits,
    ChatRecordResultProjection,
    ChatRecordStatus,
)
from apps.chatbi.repository import ChatRecordRepository


class ChatRecordError(ValueError):
    """ChatRecord 业务错误基类。"""


class ChatRecordNotFoundError(ChatRecordError):
    """ChatRecord 不存在。"""


class ChatRecordOwnershipError(ChatRecordError):
    """ChatRecord 不属于指定会话。"""


class ChatRecordTransitionError(ChatRecordError):
    """ChatRecord 状态转换不合法。"""


class ChatRecordResultTooLargeError(ChatRecordError):
    """最终结果无法在会话快照边界内安全保存。"""


_TERMINAL_STATUSES = {
    ChatRecordStatus.SUCCEEDED,
    ChatRecordStatus.FAILED,
    ChatRecordStatus.CANCELLED,
}

_ALLOWED_TRANSITIONS = {
    ChatRecordStatus.CREATED: {
        ChatRecordStatus.CREATED,
        ChatRecordStatus.RUNNING,
        ChatRecordStatus.WAITING_USER,
        *_TERMINAL_STATUSES,
    },
    ChatRecordStatus.RUNNING: {
        ChatRecordStatus.RUNNING,
        ChatRecordStatus.WAITING_USER,
        *_TERMINAL_STATUSES,
    },
    ChatRecordStatus.WAITING_USER: {
        ChatRecordStatus.WAITING_USER,
        ChatRecordStatus.RUNNING,
        *_TERMINAL_STATUSES,
    },
    ChatRecordStatus.SUCCEEDED: {ChatRecordStatus.SUCCEEDED},
    ChatRecordStatus.FAILED: {
        ChatRecordStatus.FAILED,
        ChatRecordStatus.CREATED,
        ChatRecordStatus.RUNNING,
    },
    ChatRecordStatus.CANCELLED: {ChatRecordStatus.CANCELLED},
}

_STATUS_ALIASES = {
    "created": ChatRecordStatus.CREATED,
    "running": ChatRecordStatus.RUNNING,
    "waiting_input": ChatRecordStatus.WAITING_USER,
    "waiting_user": ChatRecordStatus.WAITING_USER,
    "finished": ChatRecordStatus.SUCCEEDED,
    "succeeded": ChatRecordStatus.SUCCEEDED,
    "failed": ChatRecordStatus.FAILED,
    "cancelled": ChatRecordStatus.CANCELLED,
}


class ChatRecordService:
    """统一维护 Agent、Graph 和旧 Chat 的记录状态与最终结果。"""

    def __init__(
        self,
        repository: ChatRecordRepository,
        result_limits: ChatRecordResultLimits | None = None,
    ) -> None:
        self._repository = repository
        self._result_limits = result_limits or ChatRecordResultLimits()
        if (
            min(
                self._result_limits.max_answer_chars,
                self._result_limits.max_sql_chars,
                self._result_limits.max_chart_chars,
                self._result_limits.max_data_bytes,
            )
            <= 0
        ):
            raise ChatRecordError("CHAT_RECORD_RESULT_LIMIT_INVALID")

    def get(self, record_id: int) -> ChatRecord:
        record = self._repository.get(record_id)
        if record is None:
            raise ChatRecordNotFoundError(
                f"ChatRecord with id {record_id} not found"
            )
        return record

    def create(self, data: ChatRecordCreateData) -> ChatRecord:
        if not data.question.strip():
            raise ChatRecordError("ChatRecord question is required")
        return self._repository.create(data)

    def transition_by_id(
        self,
        record_id: int,
        status: str | ChatRecordStatus,
        *,
        expected_chat_id: int | None = None,
        trace_id: str | None = None,
        execution_type: str | ChatRecordExecutionType | None = None,
        error: str | None = None,
        result: ChatRecordResultProjection | None = None,
    ) -> ChatRecord:
        return self.transition(
            self.get(record_id),
            status,
            expected_chat_id=expected_chat_id,
            trace_id=trace_id,
            execution_type=execution_type,
            error=error,
            result=result,
        )

    def transition(
        self,
        record: ChatRecord,
        status: str | ChatRecordStatus,
        *,
        expected_chat_id: int | None = None,
        trace_id: str | None = None,
        execution_type: str | ChatRecordExecutionType | None = None,
        error: str | None = None,
        result: ChatRecordResultProjection | None = None,
    ) -> ChatRecord:
        target = normalize_chat_record_status(status)
        # 迁移前可能存在空值或非标准历史状态；只允许它们进入新的合法状态。
        current = _STATUS_ALIASES.get(record.status) if record.status else None
        if expected_chat_id is not None and record.chat_id != expected_chat_id:
            raise ChatRecordOwnershipError("CHAT_RECORD_CHAT_MISMATCH")
        if current is not None and target not in _ALLOWED_TRANSITIONS[current]:
            raise ChatRecordTransitionError(
                f"CHAT_RECORD_TRANSITION_INVALID:{current.value}->{target.value}"
            )
        if result is not None and target is not ChatRecordStatus.SUCCEEDED:
            raise ChatRecordTransitionError(
                "CHAT_RECORD_RESULT_REQUIRES_SUCCEEDED_STATUS"
            )
        if target is ChatRecordStatus.FAILED and not (error or record.error):
            raise ChatRecordTransitionError("CHAT_RECORD_FAILED_ERROR_REQUIRED")
        bounded_result = self._bounded_result(result)

        if (
            current in _TERMINAL_STATUSES or bool(record.finish)
        ) and target not in _TERMINAL_STATUSES:
            self._clear_final_result(record)
        if trace_id is not None:
            record.trace_id = trace_id
        if execution_type is not None:
            record.execution_type = ChatRecordExecutionType(execution_type).value

        record.status = target.value
        record.finish = target in _TERMINAL_STATUSES
        if record.finish:
            record.finish_time = record.finish_time or datetime.now()
        else:
            record.finish_time = None

        if target is ChatRecordStatus.SUCCEEDED:
            self._apply_result(record, bounded_result)
            record.error = None
        elif target is ChatRecordStatus.FAILED:
            record.error = error or record.error
        elif target in {
            ChatRecordStatus.CREATED,
            ChatRecordStatus.RUNNING,
            ChatRecordStatus.WAITING_USER,
            ChatRecordStatus.CANCELLED,
        }:
            record.error = None

        self._repository.save(record)
        return record

    def _bounded_result(
        self,
        result: ChatRecordResultProjection | None,
    ) -> ChatRecordResultProjection | None:
        if result is None:
            return None
        return ChatRecordResultProjection(
            answer=(
                self._bounded_text(
                    result.answer,
                    self._result_limits.max_answer_chars,
                    "ANSWER",
                )
                if result.answer is not None
                else None
            ),
            chart_answer=(
                self._bounded_text(
                    result.chart_answer,
                    self._result_limits.max_answer_chars,
                    "CHART_ANSWER",
                )
                if result.chart_answer is not None
                else None
            ),
            sql=(
                self._bounded_text(
                    result.sql,
                    self._result_limits.max_sql_chars,
                    "SQL",
                )
                if result.sql is not None
                else None
            ),
            chart=(
                self._bounded_text(
                    result.chart,
                    self._result_limits.max_chart_chars,
                    "CHART",
                )
                if result.chart is not None
                else None
            ),
            data=self._bounded_data(result.data) if result.data is not None else None,
        )

    @staticmethod
    def _apply_result(
        record: ChatRecord,
        result: ChatRecordResultProjection | None,
    ) -> None:
        if result is None:
            return
        if result.answer is not None:
            record.sql_answer = result.answer
        if result.chart_answer is not None:
            record.chart_answer = result.chart_answer
        if result.sql is not None:
            record.sql = result.sql
        if result.chart is not None:
            record.chart = result.chart
        if result.data is not None:
            record.data = result.data

    @staticmethod
    def _bounded_text(value: str, max_chars: int, field: str) -> str:
        if len(value) > max_chars:
            raise ChatRecordResultTooLargeError(
                f"CHAT_RECORD_{field}_TOO_LARGE:{len(value)}>{max_chars}"
            )
        return value

    def _bounded_data(self, value: str) -> str:
        encoded = value.encode("utf-8")
        if len(encoded) <= self._result_limits.max_data_bytes:
            return value
        try:
            payload = orjson.loads(encoded)
        except orjson.JSONDecodeError as exc:
            raise ChatRecordResultTooLargeError(
                "CHAT_RECORD_DATA_TOO_LARGE_INVALID_JSON"
            ) from exc

        if isinstance(payload, list):
            fields: list[Any] = []
            rows = payload
            row_count = len(rows)
            artifact_ref = None
        elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
            raw_fields = payload.get("fields")
            fields = raw_fields if isinstance(raw_fields, list) else []
            rows = payload["data"]
            raw_row_count = payload.get("row_count")
            row_count = (
                raw_row_count
                if isinstance(raw_row_count, int)
                and not isinstance(raw_row_count, bool)
                else len(rows)
            )
            artifact_ref = payload.get("artifact_ref")
        else:
            raise ChatRecordResultTooLargeError(
                "CHAT_RECORD_DATA_TOO_LARGE_UNSUPPORTED_STRUCTURE"
            )

        summary: dict[str, Any] = {
            "fields": fields,
            "data": [],
            "row_count": row_count,
            "stored_row_count": 0,
            "result_truncated": True,
        }
        if artifact_ref is not None:
            summary["artifact_ref"] = artifact_ref
        if len(orjson.dumps(summary)) > self._result_limits.max_data_bytes:
            raise ChatRecordResultTooLargeError("CHAT_RECORD_DATA_SUMMARY_TOO_LARGE")

        stored_rows: list[Any] = []
        for row in rows:
            candidate = {
                **summary,
                "data": [*stored_rows, row],
                "stored_row_count": len(stored_rows) + 1,
            }
            if len(orjson.dumps(candidate)) > self._result_limits.max_data_bytes:
                break
            stored_rows.append(row)
        summary["data"] = stored_rows
        summary["stored_row_count"] = len(stored_rows)
        return orjson.dumps(summary).decode()

    @staticmethod
    def _clear_final_result(record: ChatRecord) -> None:
        record.sql_answer = None
        record.chart_answer = None
        record.sql = None
        record.chart = None
        record.data = None
        record.error = None


def normalize_chat_record_status(
    status: str | ChatRecordStatus,
) -> ChatRecordStatus:
    if isinstance(status, ChatRecordStatus):
        return status
    normalized = _STATUS_ALIASES.get(status)
    if normalized is None:
        raise ChatRecordTransitionError(f"CHAT_RECORD_STATUS_INVALID:{status}")
    return normalized


__all__ = [
    "ChatRecordError",
    "ChatRecordNotFoundError",
    "ChatRecordOwnershipError",
    "ChatRecordResultTooLargeError",
    "ChatRecordService",
    "ChatRecordTransitionError",
    "normalize_chat_record_status",
]
