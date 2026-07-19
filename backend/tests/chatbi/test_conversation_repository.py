from sqlalchemy import delete
from sqlmodel import Session, select

from apps.chatbi.models import (
    Chat,
    ChatRecord,
    ConversationBinding,
    CreateChat,
    RenameChat,
)
from apps.chatbi.repository.sqlmodel import SQLModelConversationRepository
from apps.chatbi.services import ConversationService
from common.core.db import engine


class StaticBindingProvider:
    def resolve(
        self,
        *,
        workspace_id: int,
        dataset_id: int,
        assistant_type: int | None,
    ) -> ConversationBinding:
        assert workspace_id == 99101
        assert assistant_type is None
        return ConversationBinding(
            dataset_id=dataset_id,
            dataset_name="仓储测试数据集",
            datasource_id=99201,
            datasource_name="仓储测试数据源",
            datasource_type="postgresql",
            datasource_type_name="PostgreSQL",
        )


class StaticRecommendedQuestionProvider:
    def list_for_chat(self, datasource_id: int) -> list[str]:
        assert datasource_id == 99201
        return ["推荐问题一"]


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
        service = ConversationService(
            SQLModelConversationRepository(session),
            binding_provider=StaticBindingProvider(),
            recommended_question_provider=StaticRecommendedQuestionProvider(),
        )

        created = service.create(
            user_id=99301,
            workspace_id=99101,
            request=CreateChat(question="仓储生命周期测试", dataset_id=99401),
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
