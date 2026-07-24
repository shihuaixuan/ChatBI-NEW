"""旧 Chat HTTP 入口的跨模块接线。"""

from collections.abc import Callable

from sqlmodel import Session

from apps.chatbi.composition import (
    build_chat_deletion_service,
    resolve_dataset_chat_binding,
)
from apps.chatbi.services.conversation import validate_assistant_dataset_binding
from apps.chatbi.services.conversation.ports import ExecutionCleanupGateway
from apps.conversation import ChatInfo, ConversationBinding, CreateChat
from apps.conversation.composition import build_conversation_service
from apps.knowledge.recommended import build_recommended_problem_service

AgentCleanupFactory = Callable[[Session], ExecutionCleanupGateway]

_agent_cleanup_factory: AgentCleanupFactory | None = None


def configure_legacy_agent_cleanup(factory: AgentCleanupFactory) -> None:
    """注册旧会话删除所需的 Agent 执行数据清理能力。"""

    global _agent_cleanup_factory
    _agent_cleanup_factory = factory


class SemanticConversationBindingProvider:
    """把数据集执行绑定解析为 Conversation 创建输入。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        *,
        workspace_id: int,
        dataset_id: int,
        assistant_type: int | None,
    ) -> ConversationBinding:
        validate_assistant_dataset_binding(dataset_id, assistant_type)

        class _Tenant:
            oid = workspace_id

        return resolve_dataset_chat_binding(self._session, _Tenant(), dataset_id)


class RecommendedProblemProvider:
    """读取会话欢迎记录使用的推荐问题。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_chat(self, datasource_id: int) -> list[str] | None:
        return build_recommended_problem_service(
            self._session
        ).list_for_chat(datasource_id)


class LegacyChatDeletionProvider:
    """把 Agent 清理工厂接入 ChatBI 联合删除协调。"""

    def __init__(
        self,
        session: Session,
        agent_cleanup_factory: AgentCleanupFactory,
    ) -> None:
        self._service = build_chat_deletion_service(
            session,
            agent_cleanup_factory=agent_cleanup_factory,
        )

    def delete_for_user(self, user_id: int, chat_id: int) -> str:
        return self._service.delete_for_user(user_id, chat_id)


class LegacyConversationService:
    """协调数据集解析、推荐问题和 Conversation 生命周期。"""

    def __init__(self, session: Session, agent_cleanup_factory: AgentCleanupFactory) -> None:
        self._conversation_service = build_conversation_service(session)
        self._binding_provider = SemanticConversationBindingProvider(session)
        self._recommended_provider = RecommendedProblemProvider(session)
        self._deletion_provider = LegacyChatDeletionProvider(
            session,
            agent_cleanup_factory,
        )

    def create(
        self,
        *,
        user_id: int,
        workspace_id: int | None,
        request: CreateChat,
        require_dataset: bool = True,
        assistant_type: int | None = None,
    ) -> ChatInfo:
        resolved_workspace_id = 1 if workspace_id is None else workspace_id
        binding = None
        if request.dataset_id is not None:
            binding = self._binding_provider.resolve(
                workspace_id=resolved_workspace_id,
                dataset_id=request.dataset_id,
                assistant_type=assistant_type,
            )
        recommended_questions: list[str] = []
        if require_dataset and binding is not None:
            recommended_questions = (
                self._recommended_provider.list_for_chat(binding.datasource_id) or []
            )
        return self._conversation_service.create_from_request(
            user_id=user_id,
            workspace_id=resolved_workspace_id,
            request=request,
            binding=binding,
            recommended_questions=recommended_questions,
            require_dataset=require_dataset,
        )

    def delete(self, user_id: int, chat_id: int) -> str:
        return self._deletion_provider.delete_for_user(user_id, chat_id)


def build_legacy_conversation_service(session: Session) -> LegacyConversationService:
    """装配旧 HTTP 入口使用的 ChatBI 会话协调器。"""

    if _agent_cleanup_factory is None:
        raise RuntimeError("旧 Chat API 的 Agent 清理能力尚未配置")
    return LegacyConversationService(session, _agent_cleanup_factory)


__all__ = [
    "LegacyChatDeletionProvider",
    "LegacyConversationService",
    "build_legacy_conversation_service",
    "configure_legacy_agent_cleanup",
]
