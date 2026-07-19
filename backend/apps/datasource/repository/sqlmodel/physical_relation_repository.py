from sqlmodel import Session, col, select

from apps.datasource.models.dto import (
    PhysicalRelationCell,
    PhysicalRelationResources,
)
from apps.datasource.models.orm import CoreDatasource, CoreField, CoreTable


class SQLModelDatasourcePhysicalRelationRepository:
    """物理表关系图的 SQLModel 仓储实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_resources(
        self,
        datasource_id: int,
    ) -> PhysicalRelationResources | None:
        datasource = self._session.get(CoreDatasource, datasource_id)
        if datasource is None:
            return None

        table_ids = set(
            self._session.exec(
                select(CoreTable.id).where(col(CoreTable.ds_id) == datasource_id)
            ).all()
        )
        field_rows = self._session.exec(
            select(CoreField.id, CoreField.table_id).where(
                col(CoreField.ds_id) == datasource_id
            )
        ).all()
        return PhysicalRelationResources(
            cells=[
                PhysicalRelationCell.model_validate(item)
                for item in (datasource.table_relation or [])
            ],
            table_ids=table_ids,
            field_table_ids=dict(field_rows),
        )

    def save(
        self,
        datasource_id: int,
        cells: list[PhysicalRelationCell],
    ) -> None:
        datasource = self._session.get(CoreDatasource, datasource_id)
        if datasource is None:
            raise ValueError(f"Datasource {datasource_id} not found")
        try:
            datasource.table_relation = [
                cell.model_dump(mode="json", exclude_none=True) for cell in cells
            ]
            self._session.add(datasource)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
