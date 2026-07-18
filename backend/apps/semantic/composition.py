"""Semantic 公开服务的依赖装配入口。"""

from sqlmodel import Session

from apps.semantic.repository.sqlmodel.schema_loader import SemanticSchemaLoader
from apps.semantic.services.schema_service import SemanticSchemaService
from apps.semantic.services.term_query_service import SemanticTermQueryService


def build_semantic_term_query_service(
    session: Session,
) -> SemanticTermQueryService:
    """为跨领域调用方装配只读术语查询服务。"""

    return SemanticTermQueryService(
        SemanticSchemaService(SemanticSchemaLoader(session))
    )


__all__ = ["build_semantic_term_query_service"]
