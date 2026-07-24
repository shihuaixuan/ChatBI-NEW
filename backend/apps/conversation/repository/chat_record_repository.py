from typing import Protocol

from apps.conversation.models import ChatRecord, ChatRecordCreateData


class ChatRecordRepository(Protocol):
    """ChatRecord 持久化端口，提交仍由外层业务事务控制。"""

    def get(self, record_id: int) -> ChatRecord | None: ...

    def create(self, data: ChatRecordCreateData) -> ChatRecord: ...

    def save(self, record: ChatRecord) -> None: ...

    def list_recent_successful_graph(
        self,
        *,
        chat_id: int,
        exclude_record_id: int,
        user_id: int,
        dataset_id: int,
        limit: int,
    ) -> list[ChatRecord]: ...

    def list_recent_completed(
        self,
        *,
        chat_id: int,
        exclude_record_id: int,
        limit: int,
    ) -> list[ChatRecord]: ...

    def list_recent_questions(
        self,
        *,
        datasource_id: int,
        limit: int,
    ) -> list[str]: ...

    def promote_recommendation(
        self,
        chat_id: int,
        *,
        answer: str,
        questions: str,
    ) -> None: ...

    def bind_conversation_datasource(
        self,
        chat_id: int,
        *,
        datasource_id: int,
        engine_type: str,
    ) -> None: ...


__all__ = ["ChatRecordRepository"]
