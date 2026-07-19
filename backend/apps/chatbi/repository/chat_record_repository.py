from typing import Protocol

from apps.chatbi.models import ChatRecord, ChatRecordCreateData


class ChatRecordRepository(Protocol):
    """ChatRecord 持久化端口，提交仍由外层业务事务控制。"""

    def get(self, record_id: int) -> ChatRecord | None: ...

    def create(self, data: ChatRecordCreateData) -> ChatRecord: ...

    def save(self, record: ChatRecord) -> None: ...

    def promote_recommendation(
        self,
        chat_id: int,
        *,
        answer: str,
        questions: str,
    ) -> None: ...


__all__ = ["ChatRecordRepository"]
