"""Conversation 领域唯一组合入口。"""

from sqlmodel import Session

from apps.conversation.repository.sqlmodel import (
    SQLModelChatRecordRepository,
    SQLModelConversationRepository,
)
from apps.conversation.services import ChatRecordService, ConversationService


def build_chat_record_service(session: Session) -> ChatRecordService:
    """按当前数据库会话装配问数记录 Service。"""

    return ChatRecordService(SQLModelChatRecordRepository(session))


def build_conversation_service(session: Session) -> ConversationService:
    """按当前数据库会话装配会话 Service。"""

    return ConversationService(SQLModelConversationRepository(session))


__all__ = ["build_chat_record_service", "build_conversation_service"]
