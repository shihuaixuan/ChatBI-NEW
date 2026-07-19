from sqlmodel import Session

from apps.datasource.composition import (
    build_datasource_recommendation_config_store,
)
from apps.knowledge.repository.sqlmodel import SQLModelRecommendedProblemRepository
from apps.knowledge.services import RecommendedProblemService


def build_recommended_problem_service(
    session: Session,
) -> RecommendedProblemService:
    """装配推荐问题 Service 与共享事务所需的数据源配置端口。"""

    datasource_config_store = build_datasource_recommendation_config_store(session)
    return RecommendedProblemService(
        SQLModelRecommendedProblemRepository(session, datasource_config_store)
    )
