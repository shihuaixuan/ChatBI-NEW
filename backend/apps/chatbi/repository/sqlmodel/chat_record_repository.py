from datetime import datetime

from sqlmodel import Session

from apps.chatbi.models import ChatRecord, ChatRecordCreateData


class SQLModelChatRecordRepository:
    """基于 SQLModel 的 ChatRecord 仓储。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, record_id: int) -> ChatRecord | None:
        record = self._session.get(ChatRecord, record_id)
        return record if isinstance(record, ChatRecord) else None

    def create(self, data: ChatRecordCreateData) -> ChatRecord:
        record = ChatRecord(
            chat_id=data.chat_id,
            create_time=datetime.now(),
            create_by=data.user_id,
            dataset_id=data.dataset_id,
            datasource=data.datasource_id,
            engine_type=data.engine_type,
            execution_type=data.execution_type.value,
            question=data.question,
            finish=False,
            status="created",
            trace_id=data.trace_id,
        )
        self._session.add(record)
        self._session.flush()
        self._session.refresh(record)
        return record

    def save(self, record: ChatRecord) -> None:
        self._session.add(record)
        self._session.flush()


__all__ = ["SQLModelChatRecordRepository"]
