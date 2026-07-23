from sqlmodel import Session

from apps.datasource import DatasourceNotFoundError, DatasourceRecord
from apps.datasource.composition import build_datasource_service
from apps.semantic.models.dto import SemanticColumnMeta, SemanticTableMeta
from apps.semantic.repository.datasource.metadata_discovery import (
    discover_datasource_columns,
    discover_datasource_tables,
)
from apps.semantic.repository.datasource_metadata_repository import (
    DatasourceMetadataRepository,
)


class SqlModelDatasourceMetadataRepository(DatasourceMetadataRepository):
    """从 SQLModel 元数据表或实时数据源读取字段信息。"""

    def __init__(self, session: Session):
        self._session = session

    def list_accessible(self, oid: int) -> list[DatasourceRecord]:
        return build_datasource_service(self._session).list_by_workspace(oid)

    def is_accessible(self, oid: int, datasource_id: int) -> bool:
        try:
            datasource = build_datasource_service(self._session).get(datasource_id)
        except DatasourceNotFoundError:
            return False
        return datasource.oid == oid

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
