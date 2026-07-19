"""Datasource 数据库能力的公开兼容入口。

P3 期间现有调用方统一从 Datasource 领域进入，具体驱动和 SQL 实现保留在
``repository/connectors``，不再由独立的 ``apps.db`` 模块承载。
"""

from apps.datasource.repository.connectors.database import (
    check_connection,
    check_sql_read,
    exec_sql,
    get_fields,
    get_schema,
    get_session,
    get_tables,
    get_uri,
    get_uri_from_config,
    get_version,
)
from apps.datasource.repository.connectors.database_types import DB, ConnectType
from apps.datasource.repository.connectors.local_engine import (
    create_table,
    get_data_engine,
    get_engine_config,
    get_engine_conn,
    get_engine_uri,
    insert_data,
)

__all__ = [
    "ConnectType",
    "DB",
    "check_connection",
    "check_sql_read",
    "create_table",
    "exec_sql",
    "get_data_engine",
    "get_engine_config",
    "get_engine_conn",
    "get_engine_uri",
    "get_fields",
    "get_schema",
    "get_session",
    "get_tables",
    "get_uri",
    "get_uri_from_config",
    "get_version",
    "insert_data",
]
