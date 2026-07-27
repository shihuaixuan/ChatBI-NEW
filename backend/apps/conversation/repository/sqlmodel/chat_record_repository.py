from datetime import datetime

from sqlalchemy import or_
from sqlmodel import Session, col, select

from apps.conversation.models import Chat, ChatRecord, ChatRecordCreateData


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
            run_id=data.run_id or data.trace_id,
            trace_id=data.trace_id or data.run_id,
        )
        self._session.add(record)
        self._session.flush()
        self._session.refresh(record)
        return record

    def save(self, record: ChatRecord) -> None:
        self._session.add(record)
        self._session.flush()

    def list_recent_successful_graph(
        self,
        *,
        chat_id: int,
        exclude_record_id: int,
        user_id: int,
        dataset_id: int,
        limit: int,
    ) -> list[ChatRecord]:
        statement = (
            select(ChatRecord)
            .where(
                ChatRecord.chat_id == chat_id,
                ChatRecord.id != exclude_record_id,
                ChatRecord.create_by == user_id,
                ChatRecord.dataset_id == dataset_id,
                ChatRecord.execution_type == "graph",
                ChatRecord.status == "succeeded",
                col(ChatRecord.finish).is_(True),
                or_(
                    col(ChatRecord.run_id).is_not(None),
                    col(ChatRecord.trace_id).is_not(None),
                ),
            )
            .order_by(
                col(ChatRecord.create_time).desc(),
                col(ChatRecord.id).desc(),
            )
            .limit(limit)
        )
        return list(self._session.exec(statement).all())

    def list_recent_completed(
        self,
        *,
        chat_id: int,
        exclude_record_id: int,
        limit: int,
    ) -> list[ChatRecord]:
        statement = (
            select(ChatRecord)
            .where(
                ChatRecord.chat_id == chat_id,
                ChatRecord.id != exclude_record_id,
                col(ChatRecord.finish).is_(True),
            )
            .order_by(col(ChatRecord.id).desc())
            .limit(limit)
        )
        return list(self._session.exec(statement).all())

    def list_recent_questions(
        self,
        *,
        datasource_id: int,
        limit: int,
    ) -> list[str]:
        statement = (
            select(ChatRecord.question)
            .where(
                ChatRecord.datasource == datasource_id,
                col(ChatRecord.question).is_not(None),
                col(ChatRecord.error).is_(None),
            )
            .order_by(col(ChatRecord.create_time).desc())
            .limit(limit)
        )
        return [
            question
            for question in self._session.exec(statement).all()
            if isinstance(question, str) and question.strip()
        ]

    def promote_recommendation(
        self,
        chat_id: int,
        *,
        answer: str,
        questions: str,
    ) -> None:
        chat = self._session.get(Chat, chat_id)
        if not isinstance(chat, Chat):
            raise ValueError(f"Chat with id {chat_id} not found")
        chat.recommended_question_answer = answer
        chat.recommended_question = questions
        chat.recommended_generate = True
        self._session.add(chat)
        self._session.flush()

    def bind_conversation_datasource(
        self,
        chat_id: int,
        *,
        datasource_id: int,
        engine_type: str,
    ) -> None:
        chat = self._session.get(Chat, chat_id)
        if not isinstance(chat, Chat):
            raise ValueError(f"Chat with id {chat_id} not found")
        chat.datasource = datasource_id
        chat.engine_type = engine_type
        self._session.add(chat)
        self._session.flush()


__all__ = ["SQLModelChatRecordRepository"]
