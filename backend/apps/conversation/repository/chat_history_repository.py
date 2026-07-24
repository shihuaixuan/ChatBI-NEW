from datetime import datetime
from typing import Any, Protocol

from apps.conversation.models import (
    ChatLog,
    ChatLogHandle,
    ChatRecordLiveQuery,
    ChatRecordResult,
    OperationEnum,
    TypeEnum,
)


class ChatHistoryRepository(Protocol):
    """会话历史读取与执行日志读写端口，提交策略与旧实现保持一致。"""

    def list_record_projection(
        self,
        *,
        chat_id: int,
        user_id: int,
        with_data: bool,
    ) -> list[ChatRecordResult]: ...

    def get_chart_config(self, chat_record_id: int) -> dict[str, Any]: ...

    def get_data(
        self,
        chat_record_id: int,
        *,
        user_id: int | None = None,
    ) -> dict[str, Any]: ...

    def get_predict_data(
        self,
        chat_record_id: int,
        *,
        user_id: int | None = None,
    ) -> Any: ...

    def get_live_query(
        self,
        chat_record_id: int,
        *,
        user_id: int,
    ) -> ChatRecordLiveQuery | None: ...

    def get_last_execute_sql_error(self, chat_id: int) -> str | None: ...

    def list_recent_questions_for_dataset(
        self,
        *,
        dataset_id: int,
        user_id: int,
        limit: int,
    ) -> list[str]: ...

    def list_logs(self, chat_record_id: int) -> list[ChatLog]: ...

    def list_logs_by_operation(
        self,
        *,
        chat_id: int,
        operation: OperationEnum,
    ) -> list[ChatLogHandle]: ...

    def create_log(
        self,
        *,
        type_: TypeEnum,
        operate: OperationEnum | None,
        pid: int | None,
        ai_modal_id: int | None,
        base_modal: str | None,
        messages: Any,
        start_time: datetime,
        local_operation: bool,
    ) -> ChatLogHandle: ...

    def finalize_log(
        self,
        log_id: int,
        *,
        messages: Any,
        token_usage: Any,
        finish_time: datetime,
        reasoning_content: str | None,
    ) -> None: ...

    def mark_log_error(self, log_id: int) -> None: ...

    def finalize_pending_logs(
        self,
        record_id: int,
        *,
        finish_time: datetime,
    ) -> None: ...


__all__ = ["ChatHistoryRepository"]
