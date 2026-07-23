"""旧 Chat HTTP 入口的跨模块接线。

Agent 清理能力由最外层 ``apps.api`` 注入，避免 ChatBI 反向依赖尚未迁入本领域的
``apps.agent``。R4-b 将 Agent 迁入 ``chatbi/orchestration`` 后可删除这层注册。
"""

from collections.abc import Callable

from sqlmodel import Session

from apps.chatbi.composition import (
    build_chat_deletion_service,
    build_conversation_service,
    resolve_dataset_chat_binding,
)
from apps.chatbi.models import ConversationBinding
from apps.chatbi.services.conversation import (
    ConversationService,
    ExecutionCleanupGateway,
    validate_assistant_dataset_binding,
)
from apps.knowledge.recommended import build_recommended_problem_service

AgentCleanupFactory = Callable[[Session], ExecutionCleanupGateway]

_agent_cleanup_factory: AgentCleanupFactory | None = None


def configure_legacy_agent_cleanup(factory: AgentCleanupFactory) -> None:
    """注册旧会话删除所需的 Agent 执行数据清理能力。"""

    global _agent_cleanup_factory
    _agent_cleanup_factory = factory


class SemanticConversationBindingProvider:
    """把数据集执行绑定解析适配到 ChatBI 会话创建端口。"""

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


class LegacyChatDeletionProvider:
    """把注入的 Agent 清理能力接入统一会话删除服务。"""

    def __init__(
        self,
        session: Session,
        agent_cleanup: ExecutionCleanupGateway,
    ) -> None:
        self._service = build_chat_deletion_service(
            session,
            agent_cleanup=agent_cleanup,
        )

    def delete_for_user(self, user_id: int, chat_id: int) -> str:
        class _User:
            id = user_id

        return self._service.delete_for_user(_User(), chat_id)


class RecommendedProblemProvider:
    """把 Knowledge 推荐问题查询适配到 ChatBI 端口。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_chat(self, datasource_id: int) -> list[str] | None:
        return build_recommended_problem_service(
            self._session
        ).list_for_chat(datasource_id)


def build_legacy_conversation_service(session: Session) -> ConversationService:
    """装配旧 HTTP 入口需要的完整会话服务。"""

    if _agent_cleanup_factory is None:
        raise RuntimeError("旧 Chat API 的 Agent 清理能力尚未配置")
    return build_conversation_service(
        session,
        binding_provider=SemanticConversationBindingProvider(session),
        recommended_question_provider=RecommendedProblemProvider(session),
        deletion_provider=LegacyChatDeletionProvider(
            session,
            _agent_cleanup_factory(session),
        ),
    )


__all__ = [
    "LegacyChatDeletionProvider",
    "build_legacy_conversation_service",
    "configure_legacy_agent_cleanup",
]
