import json
from typing import Any, cast

from apps.datasource.models.dto import (
    ColumnSchema,
    DatasourceConf,
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
from apps.datasource.repository.connectors.database_types import DB
from apps.datasource.repository.connectors.local_engine import get_engine_config
from apps.datasource.utils.utils import aes_decrypt
from common.utils.utils import equals_ignore_case


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

    def get_database_name(self, datasource: DatasourceConnection) -> str:
        config = self._configuration(datasource)
        return config.dbSchema or config.database

    def sample_rows(
        self,
        datasource: DatasourceConnection,
        table_name: str,
        field_names: list[str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not field_names:
            return []
        database = cast(DB, DB.get_db(datasource.type))  # type: ignore[no-untyped-call]
        fields = ",".join(
            f"{database.prefix}{field}{database.suffix}"
            for field in field_names[:10]
        )
        table = f"{database.prefix}{table_name}{database.suffix}"
        if equals_ignore_case(datasource.type, "sqlServer"):
            sql = f"SELECT TOP {limit} {fields} FROM {table}"
        elif equals_ignore_case(datasource.type, "oracle", "dm"):
            sql = f"SELECT {fields} FROM {table} WHERE ROWNUM <= {limit}"
        elif equals_ignore_case(datasource.type, "ck", "hive"):
            sql = f"SELECT {fields} FROM {table_name} LIMIT {limit}"
        else:
            sql = f"SELECT {fields} FROM {table} LIMIT {limit}"
        result = self.execute_query(datasource, sql, origin_column=True)
        rows = result.get("data") or []
        if not isinstance(rows, list):
            raise ValueError("DATASOURCE_SAMPLE_RESULT_INVALID")
        return [
            cast(dict[str, Any], row)
            for row in rows[:limit]
            if isinstance(row, dict)
        ]

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

    @staticmethod
    def _configuration(datasource: DatasourceConnection) -> DatasourceConf:
        if equals_ignore_case(datasource.type, "excel"):
            return cast(
                DatasourceConf,
                get_engine_config(),  # type: ignore[no-untyped-call]
            )
        decrypted = aes_decrypt(  # type: ignore[no-untyped-call]
            datasource.configuration
        )
        return DatasourceConf(
            **json.loads(cast(str, decrypted))
        )
