from sqlalchemy import delete
from sqlmodel import Session, col, select

from apps.datasource.models.dto import DatasourceConnection, DatasourceRecord
from apps.datasource.models.orm import CoreDatasource, CoreField, CoreTable


class SQLModelDatasourceRepository:
    """数据源基本信息和本地物理元数据的 SQLModel 仓储实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_workspace(self, workspace_id: int) -> list[DatasourceRecord]:
        rows = self._session.exec(
            select(CoreDatasource)
            .where(col(CoreDatasource.oid) == workspace_id)
            .order_by(col(CoreDatasource.name))
        ).all()
        return [self._to_dto(row) for row in rows]

    def get(self, datasource_id: int) -> DatasourceRecord | None:
        row = self._session.get(CoreDatasource, datasource_id)
        return self._to_dto(row) if row is not None else None

    def name_exists(
        self,
        workspace_id: int,
        name: str,
        *,
        exclude_id: int | None = None,
    ) -> bool:
        statement = select(CoreDatasource.id).where(
            col(CoreDatasource.oid) == workspace_id,
            col(CoreDatasource.name) == name,
        )
        if exclude_id is not None:
            statement = statement.where(col(CoreDatasource.id) != exclude_id)
        return self._session.exec(statement).first() is not None

    def add_pending(self, datasource: DatasourceRecord) -> DatasourceRecord:
        row = CoreDatasource(
            **datasource.model_dump(exclude={"id"}),
        )
        try:
            self._session.add(row)
            self._session.flush()
            self._session.refresh(row)
        except Exception:
            self._session.rollback()
            raise
        return self._to_dto(row)

    def update(self, datasource: DatasourceRecord) -> DatasourceRecord:
        row = self._require(datasource.id)
        try:
            for field in (
                "name",
                "description",
                "type",
                "type_name",
                "configuration",
                "status",
                "num",
                "recommended_config",
            ):
                setattr(row, field, getattr(datasource, field))
            self._session.add(row)
            self._session.commit()
            self._session.refresh(row)
        except Exception:
            self._session.rollback()
            raise
        return self._to_dto(row)

    def delete(self, datasource_id: int) -> None:
        row = self._require(datasource_id)
        try:
            self._session.exec(
                delete(CoreField).where(col(CoreField.ds_id) == datasource_id)
            )
            self._session.exec(
                delete(CoreTable).where(col(CoreTable.ds_id) == datasource_id)
            )
            self._session.delete(row)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def rollback(self) -> None:
        self._session.rollback()

    def _require(self, datasource_id: int | None) -> CoreDatasource:
        row = (
            self._session.get(CoreDatasource, datasource_id)
            if datasource_id is not None
            else None
        )
        if row is None:
            raise ValueError(f"Datasource {datasource_id} not found")
        return row

    @staticmethod
    def _to_dto(row: CoreDatasource) -> DatasourceRecord:
        return DatasourceRecord.model_validate(row)


class SQLModelDatasourceConnectionRepository:
    """Datasource 连接快照的 SQLModel 仓储实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_connection(self, datasource_id: int) -> DatasourceConnection | None:
        row = self._session.get(CoreDatasource, datasource_id)
        if row is None:
            return None
        return DatasourceConnection(
            id=row.id,
            type=row.type,
            type_name=row.type_name,
            configuration=row.configuration,
        )
