"""ChatBI 会话联合删除协调。"""

from apps.chatbi.services.conversation.ports import (
    ArtifactCleanupGateway,
    ExecutionCleanupGateway,
    GraphCleanupGateway,
)
from apps.conversation import ConversationNotFoundError, ConversationService


class ChatDeletionService:
    """按可重试顺序协调 Conversation、Agent、Graph 和 Artifact 清理。"""

    def __init__(
        self,
        conversation_service: ConversationService,
        *,
        agent_cleanup: ExecutionCleanupGateway,
        graph_cleanup: GraphCleanupGateway,
        artifact_cleanup: ArtifactCleanupGateway,
    ) -> None:
        self._conversation_service = conversation_service
        self._agent_cleanup = agent_cleanup
        self._graph_cleanup = graph_cleanup
        self._artifact_cleanup = artifact_cleanup

    def delete_for_user(self, user_id: int, chat_id: int) -> str:
        """各步骤独立提交；失败时保留明确错误供调用方重试。"""

        try:
            self._conversation_service.get_owned(user_id, chat_id)
        except ConversationNotFoundError:
            return f"Chat with id {chat_id} has been deleted"

        run_ids = self._graph_cleanup.list_run_ids_for_chat(chat_id)
        self._artifact_cleanup.schedule_chat_cleanup(
            chat_id,
            legacy_execution_ids=run_ids,
        )
        self._graph_cleanup.delete_for_chat(chat_id)
        self._agent_cleanup.delete_for_chat(chat_id)
        result = self._conversation_service.delete(user_id, chat_id)
        self._artifact_cleanup.process_pending_cleanup()
        return result


__all__ = ["ChatDeletionService"]
