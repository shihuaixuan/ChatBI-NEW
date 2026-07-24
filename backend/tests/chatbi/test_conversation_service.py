from datetime import datetime

import pytest

from apps.conversation import (
    ChatInfo,
    ConversationBinding,
    ConversationBindingError,
    ConversationCreateData,
    ConversationOwnershipError,
    ConversationService,
    RenameChat,
)
from apps.conversation.models import Chat


class FakeConversationRepository:
    def __init__(self, chat: Chat | None = None) -> None:
        self.chat = chat
        self.created = None
        self.commits = 0
        self.rollbacks = 0
        self.fail_create = False
        self.fail_bind = False
        self.bound_datasource = None
        self.deleted = []

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

    def bind_datasource(
        self,
        chat: Chat,
        *,
        datasource_id: int,
        engine_type: str,
    ) -> Chat:
        if self.fail_bind:
            raise RuntimeError("bind failed")
        chat.datasource = datasource_id
        chat.engine_type = engine_type
        self.bound_datasource = (datasource_id, engine_type)
        return chat

    def delete(self, chat_id: int) -> None:
        self.deleted.append(chat_id)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_create_conversation_commits_chat_and_welcome_record_once():
    repository = FakeConversationRepository()
    service = ConversationService(repository)
    binding = ConversationBinding(
        dataset_id=20,
        dataset_name="销售数据集",
        datasource_id=40,
        datasource_name="销售库",
        datasource_type="mysql",
        datasource_type_name="MySQL",
    )

    result = service.create(
        ConversationCreateData(
            user_id=7,
            workspace_id=9,
            question="  查询本月销售额  ",
            origin=2,
            created_at=datetime.now(),
            binding=binding,
            create_welcome_record=True,
            recommended_questions=["本月销售额是多少？"],
        )
    )

    assert result.id == 10
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
            ConversationCreateData(
                user_id=7,
                workspace_id=9,
                question="销售额",
                origin=0,
                created_at=datetime.now(),
                binding=None,
                create_welcome_record=True,
                recommended_questions=[],
            )
        )

    assert repository.created is None
    assert repository.commits == 0


def test_create_conversation_rolls_back_repository_failure():
    repository = FakeConversationRepository()
    repository.fail_create = True
    service = ConversationService(repository)

    with pytest.raises(RuntimeError, match="create failed"):
        service.create(
            ConversationCreateData(
                user_id=7,
                workspace_id=9,
                question="无数据集会话",
                origin=0,
                created_at=datetime.now(),
                binding=None,
                create_welcome_record=False,
                recommended_questions=[],
            )
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
    repository = FakeConversationRepository(
        Chat(id=10, create_by=7, oid=9, brief="待删除", engine_type="")
    )
    service = ConversationService(repository)

    result = service.delete(7, 10)

    assert result == "Chat with id 10 has been deleted"
    assert repository.deleted == [10]


def test_owned_snapshot_enforces_user_and_workspace_without_exposing_orm():
    repository = FakeConversationRepository(
        Chat(
            id=10,
            create_by=7,
            oid=9,
            brief="销售分析",
            dataset_id=20,
            datasource=30,
            engine_type="PostgreSQL",
        )
    )
    service = ConversationService(repository)

    snapshot = service.get_owned_snapshot(
        user_id=7,
        workspace_id=9,
        chat_id=10,
    )

    assert snapshot.id == 10
    assert snapshot.dataset_id == 20
    assert snapshot.datasource_id == 30
    assert snapshot.engine_type == "PostgreSQL"
    assert snapshot.__class__.__name__ == "ConversationSnapshot"
    assert not isinstance(snapshot, Chat)

    with pytest.raises(ConversationOwnershipError):
        service.get_owned_snapshot(user_id=8, workspace_id=9, chat_id=10)
    with pytest.raises(ConversationOwnershipError):
        service.get_owned_snapshot(user_id=7, workspace_id=8, chat_id=10)


def test_bind_datasource_commits_and_returns_updated_snapshot():
    repository = FakeConversationRepository(
        Chat(id=10, create_by=7, oid=9, brief="销售分析", engine_type="")
    )
    service = ConversationService(repository)

    snapshot = service.bind_datasource(
        user_id=7,
        workspace_id=9,
        chat_id=10,
        datasource_id=30,
        engine_type="PostgreSQL",
    )

    assert repository.bound_datasource == (30, "PostgreSQL")
    assert repository.commits == 1
    assert repository.rollbacks == 0
    assert snapshot.datasource_id == 30
    assert snapshot.engine_type == "PostgreSQL"


def test_bind_datasource_rolls_back_repository_failure():
    repository = FakeConversationRepository(
        Chat(id=10, create_by=7, oid=9, brief="销售分析", engine_type="")
    )
    repository.fail_bind = True
    service = ConversationService(repository)

    with pytest.raises(RuntimeError, match="bind failed"):
        service.bind_datasource(
            user_id=7,
            workspace_id=9,
            chat_id=10,
            datasource_id=30,
            engine_type="PostgreSQL",
        )

    assert repository.commits == 0
    assert repository.rollbacks == 1
