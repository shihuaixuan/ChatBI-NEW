from types import SimpleNamespace

from sqlmodel import Session

from apps.chat.services.deletion import ChatDeletionService
from apps.chat.services.semantic_binding import (
    resolve_dataset_chat_binding,
    validate_assistant_dataset_binding,
)
from apps.chatbi.conversation import build_conversation_service as build_chatbi_service
from apps.chatbi.models import ConversationBinding
from apps.chatbi.services import ConversationService
from apps.knowledge.recommended import build_recommended_problem_service


class SemanticConversationBindingProvider:
    """把现有 Semantic 数据集绑定能力适配到 ChatBI 端口。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        *,
        workspace_id: int,
        dataset_id: int,
        assistant_type: int | None,
    ) -> ConversationBinding:
        validate_assistant_dataset_binding(dataset_id, assistant_type)
        binding = resolve_dataset_chat_binding(
            self._session,
            SimpleNamespace(oid=workspace_id),
            dataset_id,
        )
        return ConversationBinding(
            dataset_id=binding.dataset_id,
            dataset_name=binding.dataset_name,
            datasource_id=binding.datasource_id,
            datasource_name=binding.datasource_name,
            datasource_type=binding.datasource_type,
            datasource_type_name=binding.datasource_type_name,
        )


class ChatDeletionProvider:
    """保留现有 Agent、Graph 与 Artifact 级联清理能力。"""

    def __init__(self, session: Session) -> None:
        self._service = ChatDeletionService(session)

    def delete_for_user(self, user_id: int, chat_id: int) -> str:
        return self._service.delete_for_user(SimpleNamespace(id=user_id), chat_id)


def build_conversation_service(session: Session) -> ConversationService:
    """装配完整的 ChatBI 会话应用服务。"""

    return build_chatbi_service(
        session,
        binding_provider=SemanticConversationBindingProvider(session),
        recommended_question_provider=build_recommended_problem_service(session),
        deletion_provider=ChatDeletionProvider(session),
    )


__all__ = ["build_conversation_service"]
