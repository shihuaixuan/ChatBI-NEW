from datetime import datetime
from typing import Any

import orjson

from apps.conversation.errors import (
    ChatRecordError,
    ChatRecordNotFoundError,
    ChatRecordOwnershipError,
    ChatRecordResultTooLargeError,
    ChatRecordTransitionError,
)
from apps.conversation.models import (
    ChatRecord,
    ChatRecordAuxiliaryProjection,
    ChatRecordAuxiliaryType,
    ChatRecordCreateData,
    ChatRecordExecutionType,
    ChatRecordResultLimits,
    ChatRecordResultProjection,
    ChatRecordStatus,
)
from apps.conversation.repository import ChatRecordRepository

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

    def get_owned(self, user_id: int, record_id: int) -> ChatRecord:
        """读取属于指定用户的问数记录。"""

        record = self.get(record_id)
        if record.create_by != user_id:
            raise ChatRecordOwnershipError(
                f"ChatRecord with id {record_id} not owned by the current user"
            )
        return record

    def create(self, data: ChatRecordCreateData) -> ChatRecord:
        if not data.question.strip():
            raise ChatRecordError("ChatRecord question is required")
        return self._repository.create(data)

    def list_recent_successful_graph(
        self,
        *,
        chat_id: int,
        exclude_record_id: int,
        user_id: int,
        dataset_id: int,
        limit: int = 10,
    ) -> list[ChatRecord]:
        """读取 Graph 多轮上下文使用的最近成功记录。"""

        if limit <= 0:
            return []
        return self._repository.list_recent_successful_graph(
            chat_id=chat_id,
            exclude_record_id=exclude_record_id,
            user_id=user_id,
            dataset_id=dataset_id,
            limit=limit,
        )

    def list_recent_completed(
        self,
        *,
        chat_id: int,
        exclude_record_id: int,
        limit: int = 3,
    ) -> list[ChatRecord]:
        if limit <= 0:
            return []
        return self._repository.list_recent_completed(
            chat_id=chat_id,
            exclude_record_id=exclude_record_id,
            limit=limit,
        )

    def list_recent_questions(self, datasource_id: int, limit: int = 20) -> list[str]:
        if datasource_id <= 0 or limit <= 0:
            return []
        return self._repository.list_recent_questions(
            datasource_id=datasource_id,
            limit=limit,
        )

    def create_auxiliary(
        self,
        base_record: ChatRecord,
        auxiliary_type: str | ChatRecordAuxiliaryType,
    ) -> ChatRecord:
        """基于已有查询结果创建分析或预测记录。"""

        if base_record.id is None:
            raise ChatRecordError("CHAT_RECORD_AUXILIARY_SOURCE_ID_REQUIRED")
        if not (base_record.question or "").strip():
            raise ChatRecordError("CHAT_RECORD_AUXILIARY_QUESTION_REQUIRED")
        resolved_type = ChatRecordAuxiliaryType(auxiliary_type)
        execution_type = (
            ChatRecordExecutionType(base_record.execution_type)
            if base_record.execution_type in {"graph", "agent"}
            else ChatRecordExecutionType.GRAPH
        )
        record = self.create(
            ChatRecordCreateData(
                chat_id=base_record.chat_id,
                user_id=base_record.create_by,
                question=base_record.question or "",
                dataset_id=base_record.dataset_id,
                datasource_id=base_record.datasource,
                engine_type=base_record.engine_type or "",
                execution_type=execution_type,
            )
        )
        record.ai_modal_id = base_record.ai_modal_id
        if resolved_type is ChatRecordAuxiliaryType.ANALYSIS:
            record.analysis_record_id = base_record.id
        else:
            record.predict_record_id = base_record.id
        self._apply_result(
            record,
            self._bounded_result(
                ChatRecordResultProjection(
                    chart=base_record.chart,
                    data=base_record.data,
                )
            ),
        )
        self._repository.save(record)
        return record

    def project_auxiliary_by_id(
        self,
        record_id: int,
        result: ChatRecordAuxiliaryProjection,
        *,
        expected_chat_id: int | None = None,
    ) -> ChatRecord:
        return self.project_auxiliary(
            self.get(record_id),
            result,
            expected_chat_id=expected_chat_id,
        )

    def project_auxiliary(
        self,
        record: ChatRecord,
        result: ChatRecordAuxiliaryProjection,
        *,
        expected_chat_id: int | None = None,
    ) -> ChatRecord:
        """保存不改变执行状态的后处理结果，允许成功记录补写推荐问题。"""

        if expected_chat_id is not None and record.chat_id != expected_chat_id:
            raise ChatRecordOwnershipError("CHAT_RECORD_CHAT_MISMATCH")
        bounded_result = self._bounded_auxiliary(result)
        self._apply_auxiliary(record, bounded_result)
        self._repository.save(record)
        return record

    def project_recommendation_by_id(
        self,
        record_id: int,
        *,
        answer: str,
        questions: str,
        articles_number: int,
    ) -> ChatRecord:
        """保存推荐问题，并按历史规则把扩展推荐提升到会话。"""

        record = self.project_auxiliary_by_id(
            record_id,
            ChatRecordAuxiliaryProjection(
                recommended_question_answer=answer,
                recommended_question=questions,
            ),
        )
        if articles_number > 4:
            self._repository.promote_recommendation(
                record.chat_id,
                answer=record.recommended_question_answer or "",
                questions=record.recommended_question or "[]",
            )
        return record

    def bind_datasource_selection_by_id(
        self,
        record_id: int,
        *,
        datasource_id: int,
        record_engine_type: str,
        conversation_engine_type: str,
        answer: str | None,
    ) -> ChatRecord:
        """把已验证的数据源同时绑定到 ChatRecord 和所属会话。"""

        if not conversation_engine_type.strip():
            raise ChatRecordError("CHAT_DATASOURCE_ENGINE_TYPE_REQUIRED")
        record = self.get(record_id)
        bounded_result = self._bounded_auxiliary(
            ChatRecordAuxiliaryProjection(
                datasource_select_answer=answer,
                datasource_id=datasource_id,
                engine_type=record_engine_type,
            )
        )
        self._apply_auxiliary(record, bounded_result)
        self._repository.save(record)
        self._repository.bind_conversation_datasource(
            record.chat_id,
            datasource_id=datasource_id,
            engine_type=conversation_engine_type,
        )
        return record

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

    def project_result_by_id(
        self,
        record_id: int,
        result: ChatRecordResultProjection,
        *,
        expected_chat_id: int | None = None,
    ) -> ChatRecord:
        """保存执行过程中的受控结果，不改变 ChatRecord 状态。"""

        return self.project_result(
            self.get(record_id),
            result,
            expected_chat_id=expected_chat_id,
        )

    def project_result(
        self,
        record: ChatRecord,
        result: ChatRecordResultProjection,
        *,
        expected_chat_id: int | None = None,
    ) -> ChatRecord:
        """统一处理 Agent、Graph 和旧 Chat 的中间结果投影。"""

        if expected_chat_id is not None and record.chat_id != expected_chat_id:
            raise ChatRecordOwnershipError("CHAT_RECORD_CHAT_MISMATCH")
        current = _STATUS_ALIASES.get(record.status) if record.status else None
        if bool(record.finish) or current in _TERMINAL_STATUSES:
            raise ChatRecordTransitionError("CHAT_RECORD_RESULT_UPDATE_TERMINAL")
        bounded_result = self._bounded_result(result)
        self._apply_result(record, bounded_result)
        self._repository.save(record)
        return record

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

    def _bounded_auxiliary(
        self,
        result: ChatRecordAuxiliaryProjection,
    ) -> ChatRecordAuxiliaryProjection:
        if result.datasource_id is not None:
            if result.datasource_id <= 0 or not (result.engine_type or "").strip():
                raise ChatRecordError("CHAT_RECORD_DATASOURCE_BINDING_INVALID")
        elif result.engine_type is not None:
            raise ChatRecordError("CHAT_RECORD_DATASOURCE_BINDING_INVALID")
        return ChatRecordAuxiliaryProjection(
            analysis=self._bounded_optional_text(result.analysis, "ANALYSIS"),
            predict=self._bounded_optional_text(result.predict, "PREDICT"),
            predict_data=self._bounded_optional_data_blob(
                result.predict_data,
                "PREDICT_DATA",
            ),
            recommended_question_answer=self._bounded_optional_text(
                result.recommended_question_answer,
                "RECOMMENDED_QUESTION_ANSWER",
            ),
            recommended_question=self._bounded_optional_text(
                result.recommended_question,
                "RECOMMENDED_QUESTION",
            ),
            datasource_select_answer=self._bounded_optional_text(
                result.datasource_select_answer,
                "DATASOURCE_SELECT_ANSWER",
            ),
            datasource_id=result.datasource_id,
            engine_type=result.engine_type,
        )

    def _bounded_optional_text(
        self,
        value: str | None,
        field: str,
    ) -> str | None:
        if value is None:
            return None
        return self._bounded_text(
            value,
            self._result_limits.max_answer_chars,
            field,
        )

    def _bounded_optional_data_blob(
        self,
        value: str | None,
        field: str,
    ) -> str | None:
        if value is None:
            return None
        size = len(value.encode("utf-8"))
        if size > self._result_limits.max_data_bytes:
            raise ChatRecordResultTooLargeError(
                f"CHAT_RECORD_{field}_TOO_LARGE:{size}>{self._result_limits.max_data_bytes}"
            )
        return value

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
    def _apply_auxiliary(
        record: ChatRecord,
        result: ChatRecordAuxiliaryProjection,
    ) -> None:
        if result.analysis is not None:
            record.analysis = result.analysis
        if result.predict is not None:
            record.predict = result.predict
        if result.predict_data is not None:
            record.predict_data = result.predict_data
        if result.recommended_question_answer is not None:
            record.recommended_question_answer = result.recommended_question_answer
        if result.recommended_question is not None:
            record.recommended_question = result.recommended_question
        if result.datasource_select_answer is not None:
            record.datasource_select_answer = result.datasource_select_answer
        if result.datasource_id is not None:
            record.datasource = result.datasource_id
            record.engine_type = result.engine_type

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
        record.analysis = None
        record.predict = None
        record.predict_data = None
        record.recommended_question_answer = None
        record.recommended_question = None
        record.datasource_select_answer = None
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
