from __future__ import annotations

from datetime import datetime

from apps.chatbi.errors import (
    ConversationBindingError,
    ConversationError,
    ConversationNotFoundError,
    ConversationOwnershipError,
    ConversationServiceConfigurationError,
)
from apps.chatbi.models import (
    Chat,
    ChatInfo,
    ConversationBinding,
    ConversationCreateData,
    CreateChat,
    RenameChat,
)
from apps.chatbi.repository import ConversationRepository
from apps.chatbi.services.conversation.ports import (
    ConversationBindingProvider,
    ConversationDeletionProvider,
    RecommendedQuestionProvider,
)


class ConversationService:
    """统一维护会话创建、读取、重命名和删除规则。"""

    def __init__(
        self,
        repository: ConversationRepository,
        *,
        binding_provider: ConversationBindingProvider | None = None,
        recommended_question_provider: RecommendedQuestionProvider | None = None,
        deletion_provider: ConversationDeletionProvider | None = None,
    ) -> None:
        self._repository = repository
        self._binding_provider = binding_provider
        self._recommended_question_provider = recommended_question_provider
        self._deletion_provider = deletion_provider

    def get(self, chat_id: int) -> Chat:
        chat = self._repository.get(chat_id)
        if chat is None:
            raise ConversationNotFoundError(f"Chat with id {chat_id} not found")
        return chat

    def get_owned(self, user_id: int, chat_id: int) -> Chat:
        chat = self.get(chat_id)
        if chat.create_by != user_id:
            raise ConversationOwnershipError(
                f"Chat with id {chat_id} not Owned by the current user"
            )
        return chat

    def list_for_owner(self, user_id: int, workspace_id: int | None) -> list[Chat]:
        resolved_workspace_id = 1 if workspace_id is None else workspace_id
        return self._repository.list_for_owner(user_id, resolved_workspace_id)

    def create(
        self,
        *,
        user_id: int,
        workspace_id: int | None,
        request: CreateChat,
        require_dataset: bool = True,
        assistant_type: int | None = None,
    ) -> ChatInfo:
        resolved_workspace_id = 1 if workspace_id is None else workspace_id
        if request.dataset_id is None and require_dataset:
            raise ConversationBindingError("请选择数据集")

        question = (request.question or "").strip()
        if not question:
            question = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        binding = self._resolve_binding(
            workspace_id=resolved_workspace_id,
            dataset_id=request.dataset_id,
            assistant_type=assistant_type,
        )
        recommended_questions: list[str] = []
        if require_dataset and binding is not None:
            if self._recommended_question_provider is None:
                raise ConversationServiceConfigurationError(
                    "推荐问题查询端口未配置"
                )
            recommended_questions = (
                self._recommended_question_provider.list_for_chat(
                    binding.datasource_id
                )
                or []
            )

        try:
            result = self._repository.create(
                ConversationCreateData(
                    user_id=user_id,
                    workspace_id=resolved_workspace_id,
                    question=question,
                    origin=request.origin or 0,
                    created_at=datetime.now(),
                    binding=binding,
                    create_welcome_record=require_dataset,
                    recommended_questions=recommended_questions,
                )
            )
            self._repository.commit()
            return result
        except Exception:
            # 事务失败必须回滚并原样抛出，不能返回部分创建结果。
            self._repository.rollback()
            raise

    def rename(self, user_id: int, request: RenameChat) -> str:
        if request.id is None:
            raise ConversationNotFoundError("Chat id is required")
        chat = self.get_owned(user_id, request.id)
        try:
            brief = self._repository.rename(
                chat,
                brief=request.brief.strip()[:20],
                brief_generate=request.brief_generate,
            )
            self._repository.commit()
            return brief
        except Exception:
            self._repository.rollback()
            raise

    def delete(self, user_id: int, chat_id: int) -> str:
        if self._deletion_provider is None:
            raise ConversationServiceConfigurationError("会话删除端口未配置")
        return self._deletion_provider.delete_for_user(user_id, chat_id)

    def _resolve_binding(
        self,
        *,
        workspace_id: int,
        dataset_id: int | None,
        assistant_type: int | None,
    ) -> ConversationBinding | None:
        if dataset_id is None:
            return None
        if self._binding_provider is None:
            raise ConversationServiceConfigurationError("数据集绑定端口未配置")
        return self._binding_provider.resolve(
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            assistant_type=assistant_type,
        )


__all__ = [
    "ConversationBindingError",
    "ConversationBindingProvider",
    "ConversationDeletionProvider",
    "ConversationError",
    "ConversationNotFoundError",
    "ConversationOwnershipError",
    "ConversationService",
    "ConversationServiceConfigurationError",
    "RecommendedQuestionProvider",
]
