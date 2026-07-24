"""Conversation 领域唯一组合入口。"""

from sqlmodel import Session

from apps.conversation.repository.sqlmodel import (
    SQLModelChatHistoryRepository,
    SQLModelChatRecordRepository,
    SQLModelConversationRepository,
)
from apps.conversation.services import (
    ChatLogService,
    ChatRecordService,
    ConversationService,
    HistoryQueryService,
)


def build_chat_record_service(session: Session) -> ChatRecordService:
    """按当前数据库会话装配问数记录 Service。"""

    return ChatRecordService(SQLModelChatRecordRepository(session))


def build_conversation_service(session: Session) -> ConversationService:
    """按当前数据库会话装配会话 Service。"""

    return ConversationService(SQLModelConversationRepository(session))


def build_history_query_service(session: Session) -> HistoryQueryService:
    """按当前数据库会话装配会话历史查询 Service。"""

    return HistoryQueryService(
        SQLModelChatHistoryRepository(session),
        build_conversation_service(session),
        build_chat_record_service(session),
    )


def build_chat_log_service(session: Session) -> ChatLogService:
    """按当前数据库会话装配执行日志写入 Service。"""

    return ChatLogService(SQLModelChatHistoryRepository(session))


__all__ = [
    "build_chat_log_service",
    "build_chat_record_service",
    "build_conversation_service",
    "build_history_query_service",
]
