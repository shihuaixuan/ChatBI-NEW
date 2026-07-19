from sqlmodel import Session

from apps.access_control.data_policy import SessionDataPolicyProvider
from apps.capabilities.sql.execution_gateway import SqlExecuteTool
from apps.chatbi.conversation import build_conversation_reader_service
from apps.chatbi.services import (
    QueryService,
    SemanticQueryService,
    SQLPermissionService,
)
from apps.semantic.composition import build_semantic_sql_compilation_service
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


__all__ = [
    "build_conversation_reader_service",
    "build_query_service",
    "build_semantic_query_service",
]
