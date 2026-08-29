"""ChatBI 会话联合删除协调。"""

from apps.chatbi.services.conversation.ports import (
    ArtifactCleanupGateway,
    ExecutionCleanupGateway,
)
from apps.conversation import ConversationNotFoundError, ConversationService


class ChatDeletionService:
    """按可重试顺序协调 Conversation、Agent 和 Artifact 清理。"""

    def __init__(
        self,
        conversation_service: ConversationService,
        *,
        agent_cleanup: ExecutionCleanupGateway,
        artifact_cleanup: ArtifactCleanupGateway,
    ) -> None:
        self._conversation_service = conversation_service
        self._agent_cleanup = agent_cleanup
        self._artifact_cleanup = artifact_cleanup

    def delete_for_user(self, user_id: int, chat_id: int) -> str:
        """各步骤独立提交；失败时保留明确错误供调用方重试。"""

        try:
            self._conversation_service.get_owned(user_id, chat_id)
        except ConversationNotFoundError:
            return f"Chat with id {chat_id} has been deleted"

        # Graph 引擎已退役，历史 workflow_run 数据随会话删除一并遗留，
        # 后续随 workflow_* 表的 drop 迁移统一清理。
        self._artifact_cleanup.schedule_chat_cleanup(
            chat_id,
            legacy_execution_ids=[],
        )
        self._agent_cleanup.delete_for_chat(chat_id)
        result = self._conversation_service.delete(user_id, chat_id)
        self._artifact_cleanup.process_pending_cleanup()
        return result


__all__ = ["ChatDeletionService"]
