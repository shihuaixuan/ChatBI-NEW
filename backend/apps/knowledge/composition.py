from sqlmodel import Session

from apps.datasource.composition import (
    build_datasource_recommendation_config_store,
)
from apps.knowledge.repository.embedding import (
    LegacySQLExampleIndexGateway,
    LegacySQLExampleVectorSearch,
)
from apps.knowledge.repository.reference_catalog import (
    PublicSQLExampleReferenceCatalog,
)
from apps.knowledge.repository.sqlmodel import (
    SQLModelRecommendedProblemRepository,
    SQLModelSQLExampleRepository,
)
from apps.knowledge.services import (
    RecommendedProblemService,
    SQLExampleQueryService,
    SQLExampleService,
)
from common.core.config import settings


def build_recommended_problem_service(
    session: Session,
) -> RecommendedProblemService:
    """装配推荐问题 Service 与共享事务所需的数据源配置端口。"""

    datasource_config_store = build_datasource_recommendation_config_store(session)
    return RecommendedProblemService(
        SQLModelRecommendedProblemRepository(session, datasource_config_store)
    )


def build_sql_example_service(session: Session) -> SQLExampleService:
    """装配 SQL 示例源数据维护 Service。"""

    return SQLExampleService(
        SQLModelSQLExampleRepository(session),
        PublicSQLExampleReferenceCatalog(session),
        LegacySQLExampleIndexGateway(),
    )


def build_sql_example_query_service(session: Session) -> SQLExampleQueryService:
    """装配 SQL 示例召回 Service。"""

    vector_search = (
        LegacySQLExampleVectorSearch(session)
        if settings.EMBEDDING_ENABLED
        else None
    )
    return SQLExampleQueryService(
        SQLModelSQLExampleRepository(session),
        vector_search,
    )
