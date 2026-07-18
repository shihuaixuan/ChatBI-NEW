from typing import Protocol

from apps.datasource.models.datasource import CoreDatasource
from apps.semantic.models.dto import SemanticColumnMeta, SemanticTableMeta


class DatasourceMetadataRepository(Protocol):
    """语义服务访问数据源及其元数据的只读端口。"""

    def list_accessible(self, oid: int) -> list[CoreDatasource]: ...

    def is_accessible(self, oid: int, datasource_id: int) -> bool: ...

    def list_tables(self, datasource_id: int) -> list[SemanticTableMeta]: ...

    def list_columns(
        self,
        datasource_id: int,
        table_name: str,
    ) -> list[SemanticColumnMeta]: ...
