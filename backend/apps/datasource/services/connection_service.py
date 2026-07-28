from typing import Any

from apps.datasource.models.dto import ColumnSchema, TableSchema
from apps.datasource.models.dto.connection import DatasourceConnection
from apps.datasource.repository.connection_gateway import DatasourceConnectionGateway
from apps.datasource.repository.datasource_repository import (
    DatasourceConnectionRepository,
)


class DatasourceNotFoundError(ValueError):
    """请求的数据源不存在。"""

    def __init__(self, datasource_id: int) -> None:
        self.datasource_id = datasource_id
        super().__init__(f"Datasource {datasource_id} not found")


class DatasourceConnectionService:
    """数据源连接检测、元数据发现和查询执行的统一业务入口。"""

    def __init__(
        self,
        repository: DatasourceConnectionRepository,
        gateway: DatasourceConnectionGateway,
    ) -> None:
        self._repository = repository
        self._gateway = gateway

    def check_connection(self, datasource_id: int) -> bool:
        return self._gateway.check_connection(self._get_connection(datasource_id))

    def get_version(self, datasource_id: int) -> str:
        return self._gateway.get_version(self._get_connection(datasource_id))

    def list_tables(self, datasource_id: int) -> list[TableSchema]:
        return self._gateway.get_tables(self._get_connection(datasource_id))

    def list_fields(
        self,
        datasource_id: int,
        table_name: str,
    ) -> list[ColumnSchema]:
        return self._gateway.get_fields(
            self._get_connection(datasource_id),
            table_name,
        )

    def get_database_name(self, datasource_id: int) -> str:
        return self._gateway.get_database_name(
            self._get_connection(datasource_id)
        )

    def sample_rows(
        self,
        datasource_id: int,
        table_name: str,
        field_names: list[str],
        *,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            raise ValueError("DATASOURCE_SAMPLE_LIMIT_INVALID")
        return self._gateway.sample_rows(
            self._get_connection(datasource_id),
            table_name,
            field_names,
            limit=limit,
        )

    def execute_query(
        self,
        datasource_id: int,
        sql: str,
        *,
        origin_column: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        connection = self._get_connection(datasource_id)
        if timeout_seconds is None:
            return self._gateway.execute_query(
                connection,
                sql,
                origin_column=origin_column,
            )
        return self._gateway.execute_query(
            connection,
            sql,
            origin_column=origin_column,
            timeout_seconds=timeout_seconds,
        )

    def _get_connection(self, datasource_id: int) -> DatasourceConnection:
        datasource = self._repository.get_connection(datasource_id)
        if datasource is None:
            raise DatasourceNotFoundError(datasource_id)
        return datasource
