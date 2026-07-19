from sqlmodel import Session

from apps.datasource.models.orm import CoreDatasource


class SQLModelDatasourceRecommendationConfigStore:
    """在调用方共享事务中暂存数据源推荐配置。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_recommended_config(self, datasource_id: int) -> int | None:
        row = self._session.get(CoreDatasource, datasource_id)
        return row.recommended_config if row is not None else None

    def stage_recommended_config(
        self,
        datasource_id: int,
        recommended_config: int,
    ) -> bool:
        row = self._session.get(CoreDatasource, datasource_id)
        if row is None:
            return False
        row.recommended_config = recommended_config
        self._session.add(row)
        return True
