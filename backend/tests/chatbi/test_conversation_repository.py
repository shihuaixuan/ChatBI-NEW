from sqlalchemy import delete
from sqlmodel import Session, select

from apps.conversation import (
    ConversationBinding,
    ConversationService,
    CreateChat,
    RenameChat,
)
from apps.conversation.models import (
    Chat,
    ChatRecord,
)
from apps.conversation.repository.sqlmodel import SQLModelConversationRepository
from common.core.db import engine


def _cleanup(session: Session) -> None:
    record_ids = session.exec(
        select(ChatRecord.id).where(ChatRecord.create_by == 99301)
    ).all()
    if record_ids:
        session.execute(delete(ChatRecord).where(ChatRecord.id.in_(record_ids)))
    session.execute(delete(Chat).where(Chat.create_by == 99301))
    session.commit()


def test_sqlmodel_repository_supports_conversation_lifecycle():
    with Session(engine) as session:
        _cleanup(session)
        service = ConversationService(SQLModelConversationRepository(session))

        created = service.create_from_request(
            user_id=99301,
            workspace_id=99101,
            request=CreateChat(question="仓储生命周期测试", dataset_id=99401),
            binding=ConversationBinding(
                dataset_id=99401,
                dataset_name="仓储测试数据集",
                datasource_id=99201,
                datasource_name="仓储测试数据源",
                datasource_type="postgresql",
                datasource_type_name="PostgreSQL",
            ),
            recommended_questions=["推荐问题一"],
        )
        chat_id = created.id or 0

        assert chat_id > 0
        assert created.dataset_name == "仓储测试数据集"
        assert len(created.records) == 1
        assert created.records[0].execution_type == "graph"
        assert created.records[0].status == "succeeded"
        assert created.records[0].finish is True
        assert created.records[0].finish_time == created.records[0].create_time
        assert created.records[0].recommended_question == '["推荐问题一"]'
        assert service.get_owned(99301, chat_id).id == chat_id
        assert [chat.id for chat in service.list_for_owner(99301, 99101)] == [
            chat_id
        ]

        renamed = service.rename(
            99301,
            request=RenameChat(id=chat_id, brief="新标题"),
        )

        assert renamed == "新标题"
        stored_chat = session.get(Chat, chat_id)
        assert stored_chat is not None
        assert stored_chat.brief == "新标题"
        assert len(
            session.exec(
                select(ChatRecord).where(ChatRecord.chat_id == chat_id)
            ).all()
        ) == 1
        _cleanup(session)
