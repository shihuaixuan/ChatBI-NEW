"""Conversation 历史读取 Service：统一会话历史、结果、日志与用量查询。"""

from typing import Any

import orjson

from apps.conversation.errors import ConversationNotFoundError
from apps.conversation.formatting import format_json_data, format_record
from apps.conversation.models import (
    ChatLogHandle,
    ChatLogHistory,
    ChatLogHistoryItem,
    ChatRecordLiveQuery,
    OperationEnum,
)
from apps.conversation.repository.chat_history_repository import (
    ChatHistoryRepository,
)
from apps.conversation.services.chat_record import ChatRecordService
from apps.conversation.services.conversation import ConversationService


class HistoryQueryService:
    """会话历史查询的唯一入口，所有权校验统一在此处理。"""

    def __init__(
        self,
        repository: ChatHistoryRepository,
        conversation_service: ConversationService,
        chat_record_service: ChatRecordService,
    ) -> None:
        self._repository = repository
        self._conversation_service = conversation_service
        self._chat_record_service = chat_record_service

    def list_records(
        self,
        *,
        user_id: int,
        chat_id: int,
        with_data: bool = False,
    ) -> list[dict[str, Any]]:
        """读取当前用户在指定会话下的历史记录，返回已格式化的字典列表。"""

        records = self._repository.list_record_projection(
            chat_id=chat_id,
            user_id=user_id,
            with_data=with_data,
        )
        formatted = [format_record(record) for record in records]
        for row in formatted:
            try:
                data_value = row.get("data")
                if data_value is not None:
                    row["data"] = format_json_data(data_value)
            except Exception:
                pass
        return formatted

    def get_chart_config(self, chat_record_id: int) -> dict[str, Any]:
        return self._repository.get_chart_config(chat_record_id)

    def get_chart_data(
        self,
        chat_record_id: int,
        *,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        return self._repository.get_data(chat_record_id, user_id=user_id)

    def get_predict_data(
        self,
        chat_record_id: int,
        *,
        user_id: int | None = None,
    ) -> Any:
        return self._repository.get_predict_data(chat_record_id, user_id=user_id)

    def get_live_query(
        self,
        *,
        user_id: int,
        chat_record_id: int,
    ) -> ChatRecordLiveQuery | None:
        """读取用户所属记录的数据源与 SQL，供 ChatBI 重新执行。"""

        return self._repository.get_live_query(chat_record_id, user_id=user_id)

    def get_last_execute_sql_error(self, chat_id: int) -> str | None:
        return self._repository.get_last_execute_sql_error(chat_id)

    def list_recent_questions_for_dataset(
        self,
        *,
        user_id: int,
        dataset_id: int,
        limit: int = 10,
    ) -> list[str]:
        """读取当前用户在指定数据集下最近的问题。"""

        return self._repository.list_recent_questions_for_dataset(
            dataset_id=dataset_id,
            user_id=user_id,
            limit=limit,
        )

    def get_brief_generate(self, chat_id: int) -> bool:
        try:
            chat = self._conversation_service.get(chat_id)
        except ConversationNotFoundError:
            return False
        return bool(chat.brief_generate) if chat.brief_generate is not None else False

    def list_generate_sql_logs(self, chat_id: int) -> list[ChatLogHandle]:
        return self._repository.list_logs_by_operation(
            chat_id=chat_id,
            operation=OperationEnum.GENERATE_SQL,
        )

    def list_generate_chart_logs(self, chat_id: int) -> list[ChatLogHandle]:
        return self._repository.list_logs_by_operation(
            chat_id=chat_id,
            operation=OperationEnum.GENERATE_CHART,
        )

    def get_log_history(
        self,
        *,
        user_id: int,
        chat_record_id: int,
        without_steps: bool = False,
    ) -> ChatLogHistory:
        """读取记录的执行历史与用量，校验记录归当前用户所有。"""

        record = self._chat_record_service.get_owned(user_id, chat_record_id)
        logs = self._repository.list_logs(chat_record_id)

        total_tokens = 0
        steps: list[ChatLogHistoryItem | dict[str, Any]] = []

        for log in logs:
            log_tokens = 0
            if log.token_usage is not None:
                if isinstance(log.token_usage, dict):
                    if log.token_usage and "total_tokens" in log.token_usage:
                        token_value = log.token_usage["total_tokens"]
                        if isinstance(token_value, (int, float)):
                            log_tokens = int(token_value)
                elif isinstance(log.token_usage, (int, float)):
                    log_tokens = log.token_usage

            total_tokens += log_tokens

            if without_steps:
                continue

            duration = None
            if log.start_time and log.finish_time:
                try:
                    duration = round(
                        (log.finish_time - log.start_time).total_seconds(), 2
                    )
                except Exception:
                    duration = None

            operate_name = None
            message: Any = None
            if log.operate:
                if isinstance(log.operate, OperationEnum):
                    operate_name = log.operate.name
                elif isinstance(log.operate, str):
                    operate_name = log.operate
                    for enum_item in OperationEnum:
                        if enum_item.value == log.operate:
                            operate_name = enum_item.name
                            break
                else:
                    operate_name = str(log.operate)

                if log.messages is not None:
                    message = log.messages
                    if not log.operate == OperationEnum.CHOOSE_TABLE:
                        try:
                            if isinstance(
                                log.messages, (str, bytes, bytearray)
                            ):
                                message = orjson.loads(log.messages)
                        except Exception:
                            pass

            steps.append(
                ChatLogHistoryItem(
                    start_time=log.start_time,
                    finish_time=log.finish_time,
                    duration=duration,
                    total_tokens=log_tokens,
                    operate=operate_name,
                    local_operation=log.local_operation,
                    error=log.error,
                    message=message,
                )
            )

        total_duration = None
        if record.create_time and record.finish_time:
            try:
                total_duration = round(
                    (record.finish_time - record.create_time).total_seconds(), 2
                )
            except Exception:
                total_duration = None

        return ChatLogHistory(
            start_time=record.create_time,
            finish_time=record.finish_time,
            duration=total_duration,
            total_tokens=total_tokens,
            steps=steps,
        )


__all__ = ["HistoryQueryService"]
