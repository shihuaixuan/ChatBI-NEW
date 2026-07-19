from sqlalchemy import delete
from sqlmodel import Session, col, select

from apps.datasource.contracts import DatasourceRecommendationConfigStore
from apps.knowledge.errors import RecommendedProblemDatasourceNotFoundError
from apps.knowledge.models.dto import RecommendedProblemItem
from apps.knowledge.models.orm import RecommendedProblem


class SQLModelRecommendedProblemRepository:
    """推荐问题 SQLModel 仓储及跨表事务协调边界。"""

    def __init__(
        self,
        session: Session,
        datasource_config_store: DatasourceRecommendationConfigStore,
    ) -> None:
        self._session = session
        self._datasource_config_store = datasource_config_store

    def list_by_datasource(
        self,
        datasource_id: int,
    ) -> list[RecommendedProblemItem]:
        rows = self._session.exec(
            select(RecommendedProblem)
            .where(col(RecommendedProblem.datasource_id) == datasource_id)
            .order_by(
                col(RecommendedProblem.sort),
                col(RecommendedProblem.id),
            )
        ).all()
        return [RecommendedProblemItem.model_validate(row) for row in rows]

    def get_recommended_config(self, datasource_id: int) -> int | None:
        return self._datasource_config_store.get_recommended_config(datasource_id)

    def replace(
        self,
        datasource_id: int,
        recommended_config: int,
        problems: list[RecommendedProblemItem],
    ) -> None:
        try:
            updated = self._datasource_config_store.stage_recommended_config(
                datasource_id,
                recommended_config,
            )
            if not updated:
                raise RecommendedProblemDatasourceNotFoundError(datasource_id)

            self._session.exec(
                delete(RecommendedProblem).where(
                    col(RecommendedProblem.datasource_id) == datasource_id
                )
            )
            for problem in problems:
                self._session.add(
                    RecommendedProblem(
                        **problem.model_dump(exclude={"id"}),
                    )
                )
            # 数据源配置与整批推荐问题必须一次提交或一起回滚。
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
