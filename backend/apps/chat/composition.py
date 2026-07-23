"""旧 Chat 入口的会话服务接线（随 R3-d 并入 chatbi/api 组装）。"""

from sqlmodel import Session

from apps.agent.deletion import AgentExecutionDeletionService
from apps.chatbi.composition import (
    build_chat_deletion_service,
    resolve_dataset_chat_binding,
)
from apps.chatbi.composition import (
    build_conversation_service as build_chatbi_service,
)
from apps.chatbi.models import ConversationBinding
from apps.chatbi.services import ConversationService
from apps.chatbi.services.conversation import validate_assistant_dataset_binding
from apps.knowledge.recommended import build_recommended_problem_service


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


class ChatDeletionProvider:
    """保留现有 Agent、Graph 与 Artifact 级联清理能力。"""

    def __init__(self, session: Session) -> None:
        self._service = build_chat_deletion_service(
            session,
            agent_cleanup=AgentExecutionDeletionService(session),
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
        return build_recommended_problem_service(self._session).list_questions_for_chat(
            datasource_id
        )


def build_conversation_service(session: Session) -> ConversationService:
    """装配旧 Chat 入口使用的完整会话服务。"""

    return build_chatbi_service(
        session,
        binding_provider=SemanticConversationBindingProvider(session),
        recommended_question_provider=RecommendedProblemProvider(session),
        deletion_provider=ChatDeletionProvider(session),
    )
