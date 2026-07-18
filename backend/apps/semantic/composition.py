"""Semantic 公开服务的依赖装配入口。"""

from sqlmodel import Session

from apps.semantic.repository.sqlmodel.domain_repository import (
    SqlModelDomainRepository,
)
from apps.semantic.repository.sqlmodel.schema_loader import SemanticSchemaLoader
from apps.semantic.repository.sqlmodel.term_repository import SqlModelTermRepository
from apps.semantic.services.schema_service import SemanticSchemaService
from apps.semantic.services.term_compatibility_service import (
    LegacyTerminologyCompatibilityService,
)
from apps.semantic.services.term_query_service import SemanticTermQueryService
from apps.semantic.services.term_service import SemanticTermService


def build_semantic_term_query_service(
    session: Session,
) -> SemanticTermQueryService:
    """为跨领域调用方装配只读术语查询服务。"""

    return SemanticTermQueryService(
        SemanticSchemaService(SemanticSchemaLoader(session))
    )


def build_legacy_terminology_compatibility_service(
    session: Session,
) -> LegacyTerminologyCompatibilityService:
    """为旧术语 HTTP 路径装配 Semantic 权威管理服务。"""

    return LegacyTerminologyCompatibilityService(
        SemanticTermService(
            SqlModelTermRepository(session),
            SqlModelDomainRepository(session),
        )
    )


__all__ = [
    "build_legacy_terminology_compatibility_service",
    "build_semantic_term_query_service",
]
