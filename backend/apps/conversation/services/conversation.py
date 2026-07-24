"""Conversation 会话生命周期 Service。"""

from datetime import datetime

from apps.conversation.errors import (
    ConversationBindingError,
    ConversationNotFoundError,
    ConversationOwnershipError,
)
from apps.conversation.models import (
    Chat,
    ChatInfo,
    ConversationBinding,
    ConversationCreateData,
    ConversationSnapshot,
    ConversationSummary,
    CreateChat,
    RenameChat,
)
from apps.conversation.repository import ConversationRepository


class ConversationService:
    """维护会话创建、读取、重命名和自身删除规则。"""

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    def get(self, chat_id: int) -> Chat:
        chat = self._repository.get(chat_id)
        if chat is None:
            raise ConversationNotFoundError(f"Chat with id {chat_id} not found")
        return chat

    def get_owned(self, user_id: int, chat_id: int) -> Chat:
        chat = self.get(chat_id)
        if chat.create_by != user_id:
            raise ConversationOwnershipError(
                f"Chat with id {chat_id} not owned by the current user"
            )
        return chat

    def get_owned_in_workspace(
        self,
        *,
        user_id: int,
        workspace_id: int,
        chat_id: int,
    ) -> Chat:
        """校验会话同时属于指定用户和工作空间。"""

        chat = self.get_owned(user_id, chat_id)
        if chat.oid != workspace_id:
            raise ConversationOwnershipError(
                f"Chat with id {chat_id} not owned by the current workspace"
            )
        return chat

    def get_owned_snapshot(
        self,
        *,
        user_id: int,
        workspace_id: int,
        chat_id: int,
    ) -> ConversationSnapshot:
        """读取用户在指定工作空间内拥有的会话快照。"""

        chat = self.get_owned_in_workspace(
            user_id=user_id,
            workspace_id=workspace_id,
            chat_id=chat_id,
        )
        return ConversationSnapshot.model_validate(chat)

    def bind_datasource(
        self,
        *,
        user_id: int,
        workspace_id: int,
        chat_id: int,
        datasource_id: int,
        engine_type: str,
    ) -> ConversationSnapshot:
        """更新 Conversation 自有的数据源绑定并返回最新快照。"""

        if datasource_id <= 0 or not engine_type.strip():
            raise ConversationBindingError("CONVERSATION_DATASOURCE_BINDING_INVALID")
        chat = self.get_owned_in_workspace(
            user_id=user_id,
            workspace_id=workspace_id,
            chat_id=chat_id,
        )
        try:
            updated = self._repository.bind_datasource(
                chat,
                datasource_id=datasource_id,
                engine_type=engine_type,
            )
            self._repository.commit()
        except Exception:
            self._repository.rollback()
            raise
        return ConversationSnapshot.model_validate(updated)

    def list_for_owner(
        self,
        user_id: int,
        workspace_id: int | None,
    ) -> list[ConversationSummary]:
        resolved_workspace_id = 1 if workspace_id is None else workspace_id
        return [
            ConversationSummary.model_validate(chat)
            for chat in self._repository.list_for_owner(
                user_id,
                resolved_workspace_id,
            )
        ]

    def create(self, data: ConversationCreateData) -> ChatInfo:
        """保存已完成数据集解析的会话，不调用其他领域。"""

        question = data.question.strip()
        if not question:
            question = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if data.create_welcome_record and data.binding is None:
            raise ConversationBindingError("请选择数据集")

        normalized = ConversationCreateData(
            user_id=data.user_id,
            workspace_id=data.workspace_id,
            question=question,
            origin=data.origin,
            created_at=data.created_at,
            binding=data.binding,
            create_welcome_record=data.create_welcome_record,
            recommended_questions=list(data.recommended_questions),
        )
        try:
            result = self._repository.create(normalized)
            self._repository.commit()
            return result
        except Exception:
            # 会话创建失败必须回滚并原样抛出，不能返回部分结果。
            self._repository.rollback()
            raise

    def create_from_request(
        self,
        *,
        user_id: int,
        workspace_id: int | None,
        request: CreateChat,
        binding: ConversationBinding | None,
        recommended_questions: list[str] | None = None,
        require_dataset: bool = True,
    ) -> ChatInfo:
        """把 HTTP 请求转换为 Conversation 的已解析创建输入。"""

        resolved_workspace_id = 1 if workspace_id is None else workspace_id
        if request.dataset_id is None and require_dataset:
            raise ConversationBindingError("请选择数据集")
        return self.create(
            ConversationCreateData(
                user_id=user_id,
                workspace_id=resolved_workspace_id,
                question=request.question or "",
                origin=request.origin or 0,
                created_at=datetime.now(),
                binding=binding,
                create_welcome_record=require_dataset,
                recommended_questions=recommended_questions or [],
            )
        )

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
        """只删除 Conversation 拥有的 chat、chat_record 和 chat_log。"""

        self.get_owned(user_id, chat_id)
        try:
            self._repository.delete(chat_id)
            self._repository.commit()
        except Exception:
            self._repository.rollback()
            raise
        return f"Chat with id {chat_id} has been deleted"


__all__ = ["ConversationService"]
