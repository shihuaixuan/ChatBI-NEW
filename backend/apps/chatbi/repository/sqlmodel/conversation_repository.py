import json

from sqlmodel import Session, col, select

from apps.chatbi.models import (
    Chat,
    ChatInfo,
    ChatRecord,
    ConversationCreateData,
)


class SQLModelConversationRepository:
    """基于 SQLModel 的会话仓储。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, chat_id: int) -> Chat | None:
        chat = self._session.get(Chat, chat_id)
        return chat if isinstance(chat, Chat) else None

    def list_for_owner(self, user_id: int, workspace_id: int) -> list[Chat]:
        statement = (
            select(Chat)
            .where(Chat.create_by == user_id, Chat.oid == workspace_id)
            .order_by(col(Chat.create_time).desc())
        )
        return list(self._session.exec(statement).all())

    def create(self, data: ConversationCreateData) -> ChatInfo:
        binding = data.binding
        chat = Chat(
            create_time=data.created_at,
            create_by=data.user_id,
            oid=data.workspace_id,
            brief=data.question[:20],
            origin=data.origin,
            dataset_id=binding.dataset_id if binding else None,
            datasource=binding.datasource_id if binding else None,
            engine_type=binding.datasource_type_name if binding else "",
        )
        self._session.add(chat)
        self._session.flush()
        self._session.refresh(chat)

        chat_info = ChatInfo(**chat.model_dump())
        if binding is not None:
            chat_info.dataset_name = binding.dataset_name
            chat_info.dataset_exists = True
            chat_info.datasource_exists = True
            chat_info.datasource_name = binding.datasource_name
            chat_info.ds_type = binding.datasource_type

        if data.create_welcome_record and binding is not None:
            record = ChatRecord(
                execution_type="graph",
                chat_id=chat.id,
                dataset_id=binding.dataset_id,
                datasource=binding.datasource_id,
                engine_type=binding.datasource_type_name,
                first_chat=True,
                finish=True,
                create_time=data.created_at,
                create_by=data.user_id,
            )
            if data.recommended_questions:
                questions_json = json.dumps(
                    data.recommended_questions,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                record.recommended_question = questions_json
                record.recommended_question_answer = json.dumps(
                    {"content": data.recommended_questions},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            self._session.add(record)
            self._session.flush()
            self._session.refresh(record)
            chat_info.records.append(ChatRecord(**record.model_dump()))

        return chat_info

    def rename(
        self,
        chat: Chat,
        *,
        brief: str,
        brief_generate: bool,
    ) -> str:
        chat.brief = brief
        chat.brief_generate = brief_generate
        self._session.add(chat)
        self._session.flush()
        self._session.refresh(chat)
        return chat.brief

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


__all__ = ["SQLModelConversationRepository"]
