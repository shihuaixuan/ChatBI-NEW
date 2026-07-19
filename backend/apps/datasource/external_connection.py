"""外部数据源连接配置转换。"""

import json
from typing import cast

from apps.datasource.contracts import ExternalDatasource
from apps.datasource.utils.utils import aes_encrypt
from apps.datasource.repository.connectors.database_types import DB


def build_external_datasource_configuration(
    datasource: ExternalDatasource,
    timeout: int = 30,
) -> str:
    """把公开连接 DTO 转换为现有连接器使用的加密配置。"""

    configuration = {
        "host": datasource.host or "",
        "port": datasource.port or 0,
        "username": datasource.user or "",
        "password": datasource.password or "",
        "database": datasource.dataBase or "",
        "driver": "",
        "extraJdbc": "",
        "dbSchema": datasource.db_schema or "",
        "timeout": timeout or 30,
        "mode": datasource.mode or "",
    }
    return cast(
        str,
        aes_encrypt(json.dumps(configuration)),  # type: ignore[no-untyped-call]
    )


def get_database_type_name(database_type: str | None) -> str | None:
    """返回连接类型的展示名称；未知类型不作为可用连接返回。"""

    if not database_type:
        return None
    try:
        return cast(
            str,
            DB.get_db(database_type).db_name,  # type: ignore[no-untyped-call]
        )
    except ValueError:
        return None
