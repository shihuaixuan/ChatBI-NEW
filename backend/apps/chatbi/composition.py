from sqlmodel import Session

from apps.access_control.composition import build_data_policy_service
from apps.access_control.data_policy import SessionDataPolicyProvider
from apps.assistant.composition import build_assistant_service
from apps.capabilities.sql.execution_gateway import SqlExecuteTool
from apps.chatbi.adapters.embedding_ranking import (
    EmbeddingDatasourceSelectionCandidateRanker,
    EmbeddingGenerationSchemaTableRanker,
)
from apps.chatbi.adapters.question_model import build_question_model_service
from apps.chatbi.conversation import build_conversation_reader_service
from apps.chatbi.services import (
    DatasourceSelectionCandidateService,
    GenerationContextService,
    GenerationSchemaContextService,
    PhysicalSchemaService,
    QueryService,
    QuestionUnderstandingService,
    ResultArtifactService,
    SemanticQueryService,
    SemanticRetrievalGateway,
    SemanticRetrievalService,
    SQLPermissionService,
)
from apps.datasource.composition import (
    build_datasource_connection_service,
    build_datasource_metadata_service,
    build_datasource_service,
)
from apps.knowledge.composition import build_sql_example_query_service
from apps.retrieval.service import build_retrieval_service
from apps.semantic.composition import (
    build_semantic_sql_compilation_service,
    build_semantic_term_query_service,
)
from apps.workflow_engine.artifact_gateway import build_workflow_artifact_gateway
from common.core.config import settings
from common.core.db import engine


def build_query_service(
    session: Session,
    *,
    default_limit: int = 100,
    sample_rows: int = 10,
) -> QueryService:
    """装配使用真实数据权限策略的 ChatBI 查询服务。"""

    def policy_session_factory() -> Session:
        return Session(engine)

    return QueryService(
        default_limit=default_limit,
        sample_rows=sample_rows,
        permission_service=SQLPermissionService(
            policy_provider=SessionDataPolicyProvider(policy_session_factory)
        ),
        execute_tool=SqlExecuteTool(session),
    )


def build_semantic_query_service(session: Session) -> SemanticQueryService:
    """装配 Agent 与 Graph 共用的语义 SQL 编译入口。"""

    return SemanticQueryService(build_semantic_sql_compilation_service(session))


def build_semantic_retrieval_service(
    session: Session,
    *,
    retrieval_gateway: SemanticRetrievalGateway | None = None,
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
) -> GenerationSchemaContextService:
    """装配旧 Chat 与后续统一查询流程使用的物理 Schema 上下文服务。"""

    return GenerationSchemaContextService(
        datasource_service=build_datasource_service(session),
        metadata_service=build_datasource_metadata_service(session),
        connection_service=build_datasource_connection_service(session),
        data_policy_service=build_data_policy_service(session),
        table_ranker=EmbeddingGenerationSchemaTableRanker(),
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


__all__ = [
    "build_conversation_reader_service",
    "build_datasource_selection_candidate_service",
    "build_generation_context_service",
    "build_generation_schema_context_service",
    "build_physical_schema_service",
    "build_query_service",
    "build_result_artifact_service",
    "build_question_understanding_service",
    "build_semantic_query_service",
    "build_semantic_retrieval_service",
]
