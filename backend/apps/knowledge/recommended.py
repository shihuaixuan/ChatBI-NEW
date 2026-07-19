from sqlmodel import Session

from apps.datasource.composition import (
    build_datasource_recommendation_config_store,
)
from apps.knowledge.repository.sqlmodel import (
    SQLModelRecommendedProblemRepository,
)
from apps.knowledge.services import RecommendedProblemService


def build_recommended_problem_service(
    session: Session,
) -> RecommendedProblemService:
    """只装配推荐问题能力，不加载 SQL 示例相关依赖。"""

    return RecommendedProblemService(
        SQLModelRecommendedProblemRepository(
            session,
            build_datasource_recommendation_config_store(session),
        )
    )


__all__ = ["build_recommended_problem_service"]
