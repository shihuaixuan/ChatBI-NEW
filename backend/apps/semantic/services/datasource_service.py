from __future__ import annotations

from apps.datasource import DatasourceRecord
from apps.semantic.errors import SemanticForbiddenError
from apps.semantic.models.dto import SemanticColumnMeta, SemanticTableMeta
from apps.semantic.repository.datasource_metadata_repository import (
    DatasourceMetadataRepository,
)


class SemanticDatasourceService:
    """数据源语义元数据访问的应用服务。"""

    def __init__(self, repository: DatasourceMetadataRepository):
        self._repository = repository

    def list_datasources(self, oid: int) -> list[DatasourceRecord]:
        return self._repository.list_accessible(oid)

    def list_datasource_tables(
        self, oid: int, datasource_id: int
    ) -> list[SemanticTableMeta]:
        self._ensure_accessible(oid, datasource_id)
        return self._repository.list_tables(datasource_id)

    def list_datasource_columns(
        self,
        oid: int,
        datasource_id: int,
        table_name: str,
    ) -> list[SemanticColumnMeta]:
        self._ensure_accessible(oid, datasource_id)
        return self._repository.list_columns(datasource_id, table_name)

    def _ensure_accessible(self, oid: int, datasource_id: int) -> None:
        if not self._repository.is_accessible(oid, datasource_id):
            raise SemanticForbiddenError("SEMANTIC_DATASOURCE_NOT_FOUND")
