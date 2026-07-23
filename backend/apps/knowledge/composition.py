from sqlmodel import Session

from apps.knowledge.recommended import (
    build_recommended_problem_service as build_recommended_problem_service,
)
from apps.knowledge.repository.reference_catalog import (
    PublicSQLExampleReferenceCatalog,
)
from apps.knowledge.repository.sqlmodel import (
    SQLModelSQLExampleRepository,
)
from apps.knowledge.services import (
    SQLExampleQueryService,
    SQLExampleService,
)
from apps.retrieval.sources.sql_example_indexing import SQLExampleIndexCoordinator
from apps.retrieval.query.sql_example_query import SQLExampleRetriever


def build_sql_example_service(session: Session) -> SQLExampleService:
    """装配 SQL 示例源数据维护 Service。"""

    return SQLExampleService(
        SQLModelSQLExampleRepository(session),
        PublicSQLExampleReferenceCatalog(session),
        SQLExampleIndexCoordinator(session),
    )


def build_sql_example_query_service(session: Session) -> SQLExampleQueryService:
    """装配 SQL 示例召回 Service。"""

    return SQLExampleQueryService(
        SQLModelSQLExampleRepository(session),
        SQLExampleRetriever(session),
    )
