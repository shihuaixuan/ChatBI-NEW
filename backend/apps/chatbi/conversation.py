from sqlmodel import Session

from apps.chatbi.repository.sqlmodel import SQLModelConversationRepository
from apps.chatbi.services import (
    ConversationBindingProvider,
    ConversationDeletionProvider,
    ConversationService,
    RecommendedQuestionProvider,
)


def build_conversation_service(
    session: Session,
    *,
    binding_provider: ConversationBindingProvider | None = None,
    recommended_question_provider: RecommendedQuestionProvider | None = None,
    deletion_provider: ConversationDeletionProvider | None = None,
) -> ConversationService:
    """在 ChatBI 边界内装配仓储和外部端口。"""

    return ConversationService(
        SQLModelConversationRepository(session),
        binding_provider=binding_provider,
        recommended_question_provider=recommended_question_provider,
        deletion_provider=deletion_provider,
    )


def build_conversation_reader_service(session: Session) -> ConversationService:
    """装配只需要会话读取与所有权校验的 Service。"""

    return build_conversation_service(session)


__all__ = ["build_conversation_reader_service", "build_conversation_service"]
