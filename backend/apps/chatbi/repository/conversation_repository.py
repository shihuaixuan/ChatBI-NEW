from typing import Protocol

from apps.chatbi.models import Chat, ChatInfo, ConversationCreateData


class ConversationRepository(Protocol):
    """会话持久化端口，事务提交由应用 Service 控制。"""

    def get(self, chat_id: int) -> Chat | None: ...

    def list_for_owner(self, user_id: int, workspace_id: int) -> list[Chat]: ...

    def create(self, data: ConversationCreateData) -> ChatInfo: ...

    def rename(
        self,
        chat: Chat,
        *,
        brief: str,
        brief_generate: bool,
    ) -> str: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


__all__ = [
    "ConversationRepository",
]
