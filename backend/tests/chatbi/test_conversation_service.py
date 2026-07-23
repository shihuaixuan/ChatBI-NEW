import pytest

from apps.chatbi.models import (
    Chat,
    ChatInfo,
    ConversationBinding,
    CreateChat,
    RenameChat,
)
from apps.chatbi.services import (
    ConversationBindingError,
    ConversationOwnershipError,
    ConversationService,
)


class FakeConversationRepository:
    def __init__(self, chat: Chat | None = None) -> None:
        self.chat = chat
        self.created = None
        self.commits = 0
        self.rollbacks = 0
        self.fail_create = False

    def get(self, chat_id: int) -> Chat | None:
        if self.chat is not None and self.chat.id == chat_id:
            return self.chat
        return None

    def list_for_owner(self, user_id: int, workspace_id: int) -> list[Chat]:
        if (
            self.chat is not None
            and self.chat.create_by == user_id
            and self.chat.oid == workspace_id
        ):
            return [self.chat]
        return []

    def create(self, data):
        if self.fail_create:
            raise RuntimeError("create failed")
        self.created = data
        return ChatInfo(
            id=10,
            create_by=data.user_id,
            brief=data.question[:20],
            dataset_id=data.binding.dataset_id if data.binding else None,
        )

    def rename(self, chat: Chat, *, brief: str, brief_generate: bool) -> str:
        chat.brief = brief
        chat.brief_generate = brief_generate
        return brief

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeBindingProvider:
    def __init__(self) -> None:
        self.calls = []

    def resolve(self, *, workspace_id, dataset_id, assistant_type):
        self.calls.append((workspace_id, dataset_id, assistant_type))
        return ConversationBinding(
            dataset_id=dataset_id,
            dataset_name="销售数据集",
            datasource_id=40,
            datasource_name="销售库",
            datasource_type="mysql",
            datasource_type_name="MySQL",
        )


class FakeRecommendedQuestionProvider:
    def list_for_chat(self, datasource_id: int) -> list[str]:
        assert datasource_id == 40
        return ["本月销售额是多少？"]


class FakeDeletionProvider:
    def __init__(self) -> None:
        self.calls = []

    def delete_for_user(self, user_id: int, chat_id: int) -> str:
        self.calls.append((user_id, chat_id))
        return f"Chat with id {chat_id} has been deleted"


def test_create_conversation_commits_chat_and_welcome_record_once():
    repository = FakeConversationRepository()
    binding_provider = FakeBindingProvider()
    service = ConversationService(
        repository,
        binding_provider=binding_provider,
        recommended_question_provider=FakeRecommendedQuestionProvider(),
    )

    result = service.create(
        user_id=7,
        workspace_id=9,
        request=CreateChat(
            question="  查询本月销售额  ",
            dataset_id=20,
            origin=2,
        ),
        assistant_type=0,
    )

    assert result.id == 10
    assert binding_provider.calls == [(9, 20, 0)]
    assert repository.created.question == "查询本月销售额"
    assert repository.created.create_welcome_record is True
    assert repository.created.recommended_questions == ["本月销售额是多少？"]
    assert repository.commits == 1
    assert repository.rollbacks == 0


def test_create_conversation_requires_dataset_without_partial_write():
    repository = FakeConversationRepository()
    service = ConversationService(repository)

    with pytest.raises(ConversationBindingError, match="请选择数据集"):
        service.create(
            user_id=7,
            workspace_id=9,
            request=CreateChat(question="销售额"),
        )

    assert repository.created is None
    assert repository.commits == 0


def test_create_conversation_rolls_back_repository_failure():
    repository = FakeConversationRepository()
    repository.fail_create = True
    service = ConversationService(repository)

    with pytest.raises(RuntimeError, match="create failed"):
        service.create(
            user_id=7,
            workspace_id=9,
            request=CreateChat(question="无数据集会话"),
            require_dataset=False,
        )

    assert repository.commits == 0
    assert repository.rollbacks == 1


def test_rename_enforces_owner_and_title_length():
    chat = Chat(id=10, create_by=7, oid=9, brief="旧标题", engine_type="")
    repository = FakeConversationRepository(chat)
    service = ConversationService(repository)

    requested_brief = "这是一个超过二十个字符的会话标题用于长度校验"
    brief = service.rename(
        7,
        RenameChat(id=10, brief=f"  {requested_brief}  "),
    )

    assert brief == requested_brief[:20]
    assert len(brief) == 20
    assert repository.commits == 1

    with pytest.raises(ConversationOwnershipError):
        service.get_owned(8, 10)


def test_delete_delegates_owned_conversation_cleanup():
    deletion_provider = FakeDeletionProvider()
    service = ConversationService(
        FakeConversationRepository(),
        deletion_provider=deletion_provider,
    )

    result = service.delete(7, 10)

    assert result == "Chat with id 10 has been deleted"
    assert deletion_provider.calls == [(7, 10)]


def test_xpack_chat_models_export_canonical_chatbi_objects():
    from apps.chat.models import chat_model
    from apps.chatbi import models

    assert chat_model.Chat is models.Chat
    assert chat_model.ChatRecord is models.ChatRecord
    assert chat_model.ChatLog is models.ChatLog
    assert chat_model.ChatInfo is models.ChatInfo
