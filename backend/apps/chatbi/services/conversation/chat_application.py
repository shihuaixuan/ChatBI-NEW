"""ChatBI 会话应用编排：创建绑定解析与联合删除协调。

Conversation 只拥有会话数据本身；数据集绑定、推荐问题与跨模块清理
由 ChatBI 应用层在此组装，再调用 Conversation 公开 Service。
"""

from __future__ import annotations

from collections.abc import Callable

from apps.chatbi.services.conversation.dataset_binding import (
    validate_assistant_dataset_binding,
)
from apps.chatbi.services.conversation.deletion_service import ChatDeletionService
from apps.conversation import (
    ChatInfo,
    ConversationBinding,
    ConversationService,
    CreateChat,
)


class ChatApplicationService:
    """协调数据集解析、推荐问题与会话生命周期。"""

    def __init__(
        self,
        *,
        conversation_service: ConversationService,
        resolve_binding: Callable[[object, int], ConversationBinding],
        list_recommended_questions: Callable[[int], list[str] | None],
        deletion_service: ChatDeletionService,
    ) -> None:
        self._conversation_service = conversation_service
        self._resolve_binding = resolve_binding
        self._list_recommended_questions = list_recommended_questions
        self._deletion_service = deletion_service

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
        binding = None
        if request.dataset_id is not None:
            validate_assistant_dataset_binding(request.dataset_id, assistant_type)

            class _Tenant:
                oid = resolved_workspace_id

            binding = self._resolve_binding(_Tenant(), request.dataset_id)
        recommended_questions: list[str] = []
        if require_dataset and binding is not None:
            recommended_questions = (
                self._list_recommended_questions(binding.datasource_id) or []
            )
        return self._conversation_service.create_from_request(
            user_id=user_id,
            workspace_id=resolved_workspace_id,
            request=request,
            binding=binding,
            recommended_questions=recommended_questions,
            require_dataset=require_dataset,
        )

    def delete(self, user_id: int, chat_id: int) -> str:
        return self._deletion_service.delete_for_user(user_id, chat_id)


__all__ = ["ChatApplicationService"]
