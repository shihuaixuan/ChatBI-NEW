"""会话子域端口（AGENTS.md v2 §5）。

仓储端口（ConversationRepository / ChatRecordRepository）位于 apps/chatbi/repository/。
本文件承载会话流程依赖的跨域/防腐端口；Provider 后缀为历史命名，统一改名安排在 R1-d。
"""

from __future__ import annotations

from typing import Protocol

from apps.chatbi.models import ConversationBinding


class ConversationBindingProvider(Protocol):
    def resolve(
        self,
        *,
        workspace_id: int,
        dataset_id: int,
        assistant_type: int | None,
    ) -> ConversationBinding: ...


class RecommendedQuestionProvider(Protocol):
    def list_for_chat(self, datasource_id: int) -> list[str] | None: ...


class ConversationDeletionProvider(Protocol):
    def delete_for_user(self, user_id: int, chat_id: int) -> str: ...


__all__ = [
    "ConversationBindingProvider",
    "ConversationDeletionProvider",
    "RecommendedQuestionProvider",
]
