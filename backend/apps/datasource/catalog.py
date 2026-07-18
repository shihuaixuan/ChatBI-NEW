"""Datasource 摘要目录的 SQLModel 实现与装配入口。"""

from sqlmodel import Session, col, select

from apps.datasource.contracts import DatasourceCatalog, DatasourceSummary
from apps.datasource.models.datasource import CoreDatasource


class SQLModelDatasourceCatalog:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_workspace(
        self,
        workspace_id: int,
        datasource_ids: list[int] | None = None,
    ) -> list[DatasourceSummary]:
        statement = select(CoreDatasource).where(
            col(CoreDatasource.oid) == workspace_id
        )
        if datasource_ids is not None:
            if not datasource_ids:
                return []
            statement = statement.where(col(CoreDatasource.id).in_(datasource_ids))

        rows = self._session.exec(statement.order_by(col(CoreDatasource.name))).all()
        return [
            DatasourceSummary(
                id=row.id,
                name=row.name,
                description=row.description,
                type=row.type,
                type_name=row.type_name,
                num=row.num,
            )
            for row in rows
        ]


def build_datasource_catalog(session: Session) -> DatasourceCatalog:
    return SQLModelDatasourceCatalog(session)
