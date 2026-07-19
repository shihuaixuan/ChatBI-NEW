from sqlmodel import Session

from apps.chatbi.repository.sqlmodel import SQLModelChatRecordRepository
from apps.chatbi.services import ChatRecordService


def build_chat_record_service(session: Session) -> ChatRecordService:
    """在 ChatBI 边界内装配 ChatRecord 仓储。"""

    return ChatRecordService(SQLModelChatRecordRepository(session))


__all__ = ["build_chat_record_service"]
