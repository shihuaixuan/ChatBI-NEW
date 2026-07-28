from typing import Any, Protocol

from apps.datasource.models.dto import (
    ColumnSchema,
    DatasourceConnection,
    TableSchema,
)


class DatasourceConnectionGateway(Protocol):
    """数据库驱动实现必须提供的统一能力。"""

    def check_connection(self, datasource: DatasourceConnection) -> bool: ...

    def get_version(self, datasource: DatasourceConnection) -> str: ...

    def get_tables(self, datasource: DatasourceConnection) -> list[TableSchema]: ...

    def get_fields(
        self,
        datasource: DatasourceConnection,
        table_name: str,
    ) -> list[ColumnSchema]: ...

    def get_database_name(self, datasource: DatasourceConnection) -> str: ...

    def sample_rows(
        self,
        datasource: DatasourceConnection,
        table_name: str,
        field_names: list[str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def execute_query(
        self,
        datasource: DatasourceConnection,
        sql: str,
        *,
        origin_column: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]: ...
