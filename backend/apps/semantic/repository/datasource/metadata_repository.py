from sqlmodel import Session, col, select

from apps.datasource.models.datasource import CoreDatasource
from apps.semantic.models.dto import SemanticColumnMeta, SemanticTableMeta
from apps.semantic.repository.datasource.metadata_discovery import (
    discover_datasource_columns,
    discover_datasource_tables,
)
from apps.semantic.repository.datasource_metadata_repository import (
    DatasourceMetadataRepository,
)
from apps.semantic.repository.sqlmodel.results import all_results


class SqlModelDatasourceMetadataRepository(DatasourceMetadataRepository):
    """从 SQLModel 元数据表或实时数据源读取字段信息。"""

    def __init__(self, session: Session):
        self._session = session

    def list_accessible(self, oid: int) -> list[CoreDatasource]:
        return all_results(
            self._session.exec(
                select(CoreDatasource)
                .where(CoreDatasource.oid == oid)
                .order_by(col(CoreDatasource.id))
            )
        )

    def is_accessible(self, oid: int, datasource_id: int) -> bool:
        datasource = self._session.get(CoreDatasource, datasource_id)
        return datasource is not None and datasource.oid == oid

    def list_tables(self, datasource_id: int) -> list[SemanticTableMeta]:
        return discover_datasource_tables(self._session, datasource_id)

    def list_columns(
        self,
        datasource_id: int,
        table_name: str,
    ) -> list[SemanticColumnMeta]:
        return discover_datasource_columns(
            self._session,
            datasource_id,
            table_name,
        )
