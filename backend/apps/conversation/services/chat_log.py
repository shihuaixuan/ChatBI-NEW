"""Conversation 执行日志写入 Service：会话步骤日志的开始、结束与置错。"""

from datetime import datetime
from typing import Any

from apps.conversation.models import ChatLogHandle, OperationEnum, TypeEnum
from apps.conversation.repository.chat_history_repository import (
    ChatHistoryRepository,
)


class ChatLogService:
    """维护会话步骤日志的生命周期，向调用方返回可传递句柄。"""

    def __init__(self, repository: ChatHistoryRepository) -> None:
        self._repository = repository

    def start_log(
        self,
        *,
        operate: OperationEnum | None = None,
        record_id: int | None = None,
        ai_modal_id: int | None = None,
        ai_modal_name: str | None = None,
        full_message: Any = None,
        local_operation: bool = False,
    ) -> ChatLogHandle:
        return self._repository.create_log(
            type_=TypeEnum.CHAT,
            operate=operate,
            pid=record_id,
            ai_modal_id=ai_modal_id,
            base_modal=ai_modal_name,
            messages=full_message,
            start_time=datetime.now(),
            local_operation=local_operation,
        )

    def end_log(
        self,
        log: ChatLogHandle,
        full_message: Any,
        *,
        reasoning_content: str | None = None,
        token_usage: Any = None,
    ) -> ChatLogHandle:
        if log.id is None:
            raise ValueError("ChatLogHandle without id cannot be finalized")
        if token_usage is None:
            token_usage = {}
        finish_time = datetime.now()
        reasoning = (
            reasoning_content
            if reasoning_content and len(reasoning_content.strip()) > 0
            else None
        )
        self._repository.finalize_log(
            log.id,
            messages=full_message,
            token_usage=token_usage,
            finish_time=finish_time,
            reasoning_content=reasoning,
        )
        log.messages = full_message
        log.token_usage = token_usage
        log.finish_time = finish_time
        log.reasoning_content = reasoning
        return log

    def trigger_error(self, log: ChatLogHandle) -> ChatLogHandle:
        if log.id is None:
            raise ValueError("ChatLogHandle without id cannot be marked as error")
        self._repository.mark_log_error(log.id)
        log.error = True
        return log

    def finalize_pending_logs(self, record_id: int, finish_time: datetime) -> None:
        """终结记录下所有未完成步骤日志，与旧错误终态行为保持一致。"""

        self._repository.finalize_pending_logs(record_id, finish_time=finish_time)


__all__ = ["ChatLogService"]
