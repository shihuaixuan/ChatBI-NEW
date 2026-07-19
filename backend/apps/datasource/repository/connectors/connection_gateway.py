from typing import Any, cast

from apps.datasource.models.dto import (
    ColumnSchema,
    DatasourceConnection,
    TableSchema,
)
from apps.datasource.repository.connectors.database import (
    check_connection,
    exec_sql,
    get_fields,
    get_tables,
    get_version,
)


class DatabaseDriverConnectionGateway:
    """现有多数据库驱动实现的统一网关适配器。"""

    def check_connection(self, datasource: DatasourceConnection) -> bool:
        return cast(bool, check_connection(None, datasource))

    def get_version(self, datasource: DatasourceConnection) -> str:
        return cast(str, get_version(datasource))

    def get_tables(self, datasource: DatasourceConnection) -> list[TableSchema]:
        return cast(list[TableSchema], get_tables(datasource))

    def get_fields(
        self,
        datasource: DatasourceConnection,
        table_name: str,
    ) -> list[ColumnSchema]:
        return cast(list[ColumnSchema], get_fields(datasource, table_name))

    def execute_query(
        self,
        datasource: DatasourceConnection,
        sql: str,
        *,
        origin_column: bool = False,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            exec_sql(datasource, sql, origin_column=origin_column),
        )
