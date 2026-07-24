from collections.abc import Callable

from sqlmodel import Session

from apps.access_control.composition import build_data_policy_service
from apps.access_control.data_policy import SessionDataPolicyProvider
from apps.assistant.composition import build_assistant_service
from apps.chatbi.adapters.embedding_ranking import (
    EmbeddingDatasourceSelectionCandidateRanker,
    EmbeddingSchemaRankingClient,
)
from apps.chatbi.adapters.execution import DatasourceQueryExecutor
from apps.chatbi.adapters.execution_cleanup import (
    CommittedAgentCleanupGateway,
    WorkflowArtifactCleanupGateway,
    WorkflowRunCleanupGateway,
)
from apps.chatbi.adapters.question_model import build_question_model_service
from apps.chatbi.services.conversation import (
    resolve_conversation_binding,
)
from apps.chatbi.services.conversation.deletion_service import (
    ChatDeletionService,
)
from apps.chatbi.services.conversation.ports import ExecutionCleanupGateway
from apps.chatbi.services.execution import (
    GuardedQueryService,
    ResultArtifactService,
    SQLPermissionService,
)
from apps.chatbi.services.generation import (
    GenerationContextService,
    SchemaContextService,
)
from apps.chatbi.services.planning import (
    DatasourceSelectionCandidateService,
    PhysicalSchemaService,
    SemanticCompilationService,
    SemanticRetrievalService,
)
from apps.chatbi.services.understanding import QuestionUnderstandingService
from apps.conversation import ConversationBinding
from apps.conversation.composition import (
    build_chat_record_service as build_conversation_chat_record_service,
)
from apps.conversation.composition import (
    build_conversation_service as build_owned_conversation_service,
)
from apps.conversation.services import ChatRecordService, ConversationService
from apps.datasource.composition import (
    build_datasource_connection_service,
    build_datasource_metadata_service,
    build_datasource_service,
)
from apps.knowledge.composition import build_sql_example_query_service
from apps.retrieval.query.service import RetrievalService, build_retrieval_service
from apps.semantic.composition import (
    build_semantic_dataset_binding_service,
    build_semantic_sql_compilation_service,
    build_semantic_term_query_service,
)
from common.core.config import settings
from common.core.db import engine
from sqlbot_platform.workflow_engine.artifact_gateway import (
    build_workflow_artifact_gateway,
)


def build_query_service(
    session: Session,
    *,
    default_limit: int = 100,
    sample_rows: int = 10,
) -> GuardedQueryService:
    """装配使用真实数据权限策略的 ChatBI 查询服务。"""

    def policy_session_factory() -> Session:
        return Session(engine)

    return GuardedQueryService(
        default_limit=default_limit,
        sample_rows=sample_rows,
        permission_service=SQLPermissionService(
            policy_provider=SessionDataPolicyProvider(policy_session_factory)
        ),
        execute_tool=DatasourceQueryExecutor(session),
    )


def build_semantic_query_service(session: Session) -> SemanticCompilationService:
    """装配 Agent 与 Graph 共用的语义 SQL 编译入口。"""

    return SemanticCompilationService(build_semantic_sql_compilation_service(session))


def build_semantic_retrieval_service(
    session: Session,
    *,
    retrieval_gateway: RetrievalService | None = None,
) -> SemanticRetrievalService:
    """装配 Agent 与 Graph 共用的语义资产检索入口。"""

    gateway = retrieval_gateway or build_retrieval_service(session)
    return SemanticRetrievalService(gateway)


def build_physical_schema_service(session: Session) -> PhysicalSchemaService:
    """装配 Agent 读取物理表字段的 ChatBI 服务。"""

    return PhysicalSchemaService(build_datasource_metadata_service(session))


def build_result_artifact_service(session: Session) -> ResultArtifactService:
    """装配 ChatBI 统一结果 Artifact Service。"""

    return ResultArtifactService(build_workflow_artifact_gateway(session))


def build_generation_context_service(
    session: Session,
) -> GenerationContextService:
    """装配旧 Chat 和后续统一查询流程使用的知识上下文服务。"""

    return GenerationContextService(
        sql_example_service=build_sql_example_query_service(session),
        term_query_service=build_semantic_term_query_service(session),
    )


def build_generation_schema_context_service(
    session: Session,
) -> SchemaContextService:
    """装配旧 Chat 与后续统一查询流程使用的物理 Schema 上下文服务。"""

    return SchemaContextService(
        datasource_service=build_datasource_service(session),
        metadata_service=build_datasource_metadata_service(session),
        connection_service=build_datasource_connection_service(session),
        data_policy_service=build_data_policy_service(session),
        table_ranker=EmbeddingSchemaRankingClient(),
        embedding_enabled=settings.TABLE_EMBEDDING_ENABLED,
        embedding_limit=settings.TABLE_EMBEDDING_COUNT,
    )


def build_datasource_selection_candidate_service(
    session: Session,
) -> DatasourceSelectionCandidateService:
    """装配生成流程使用的数据源候选范围和排序服务。"""

    return DatasourceSelectionCandidateService(
        assistant_service=build_assistant_service(session),
        datasource_service=build_datasource_service(session),
        ranker=EmbeddingDatasourceSelectionCandidateRanker(),
        embedding_enabled=settings.TABLE_EMBEDDING_ENABLED,
        embedding_limit=settings.DS_EMBEDDING_COUNT,
    )


def build_question_understanding_service() -> QuestionUnderstandingService:
    """装配 Agent 使用的严格问题理解服务。"""

    return QuestionUnderstandingService(
        question_model_service=build_question_model_service()
    )



def build_chat_record_service(session: Session) -> ChatRecordService:
    """从 Conversation 组合入口获取 ChatRecord Service。"""

    return build_conversation_chat_record_service(session)


def build_conversation_service(
    session: Session,
) -> ConversationService:
    """从 Conversation 组合入口获取会话 Service。"""

    return build_owned_conversation_service(session)


def build_conversation_reader_service(session: Session) -> ConversationService:
    """装配只需要会话读取与所有权校验的 Service。"""

    return build_conversation_service(session)



def resolve_dataset_chat_binding(
    session: Session,
    current_user: object,
    dataset_id: int | None,
) -> ConversationBinding:
    """旧签名兼容：按当前用户工作空间解析数据集执行绑定。"""

    workspace_id = getattr(current_user, "oid", None)
    return resolve_conversation_binding(
        build_semantic_dataset_binding_service(session),
        build_datasource_service(session),
        workspace_id=workspace_id if workspace_id is not None else 1,
        dataset_id=dataset_id,
    )


def build_chat_deletion_service(
    session: Session,
    *,
    agent_cleanup_factory: Callable[[Session], ExecutionCleanupGateway],
) -> ChatDeletionService:
    """装配使用独立模块事务的会话联合删除服务。"""

    def cleanup_session_factory() -> Session:
        return Session(engine)

    return ChatDeletionService(
        build_conversation_service(session),
        agent_cleanup=CommittedAgentCleanupGateway(
            cleanup_session_factory,
            agent_cleanup_factory,
        ),
        graph_cleanup=WorkflowRunCleanupGateway(cleanup_session_factory),
        artifact_cleanup=WorkflowArtifactCleanupGateway(
            cleanup_session_factory,
            build_result_artifact_service,
        ),
    )


__all__ = [
    "build_chat_deletion_service",
    "build_chat_record_service",
    "build_conversation_reader_service",
    "build_conversation_service",
    "build_datasource_selection_candidate_service",
    "build_generation_context_service",
    "build_generation_schema_context_service",
    "build_physical_schema_service",
    "build_query_service",
    "build_result_artifact_service",
    "build_question_understanding_service",
    "resolve_dataset_chat_binding",
    "build_semantic_query_service",
    "build_semantic_retrieval_service",
]
