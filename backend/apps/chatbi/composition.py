from sqlmodel import Session

from apps.access_control.data_policy import SessionDataPolicyProvider
from apps.capabilities.sql.execution_gateway import SqlExecuteTool
from apps.chatbi.conversation import build_conversation_reader_service
from apps.chatbi.services import (
    PhysicalSchemaService,
    QueryService,
    QuestionUnderstandingService,
    SemanticQueryService,
    SemanticRetrievalGateway,
    SemanticRetrievalService,
    SQLPermissionService,
)
from apps.datasource.composition import build_datasource_metadata_service
from apps.retrieval.service import build_retrieval_service
from apps.semantic.composition import build_semantic_sql_compilation_service
from common.core.db import engine
from infrastructure.question_model import build_question_model_service


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


def build_question_understanding_service() -> QuestionUnderstandingService:
    """装配 Agent 使用的严格问题理解服务。"""

    return QuestionUnderstandingService(
        question_model_service=build_question_model_service()
    )


__all__ = [
    "build_conversation_reader_service",
    "build_physical_schema_service",
    "build_query_service",
    "build_question_understanding_service",
    "build_semantic_query_service",
    "build_semantic_retrieval_service",
]
