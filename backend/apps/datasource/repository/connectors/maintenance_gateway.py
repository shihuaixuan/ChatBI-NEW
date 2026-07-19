import json

from sqlalchemy import text

from apps.datasource.models.dto import DatasourceConf, DatasourceRecord
from apps.datasource.repository.connectors.database_types import DB
from apps.datasource.repository.connectors.local_engine import get_engine_conn
from apps.datasource.utils.utils import aes_decrypt


class DatabaseDatasourceMaintenanceGateway:
    """基于数据库连接实现类型解析和 Excel 物理表清理。"""

    def get_type_name(self, datasource_type: str) -> str:
        return str(DB.get_db(datasource_type).db_name)  # type: ignore[no-untyped-call]

    def cleanup(self, datasource: DatasourceRecord) -> None:
        if datasource.type != "excel":
            return

        configuration = DatasourceConf(
            **json.loads(
                aes_decrypt(datasource.configuration)  # type: ignore[no-untyped-call]
            )
        )
        engine = get_engine_conn()  # type: ignore[no-untyped-call]
        with engine.begin() as connection:
            preparer = connection.dialect.identifier_preparer
            for sheet in configuration.sheets:
                table_name = str(sheet["tableName"])
                # 表名来自持久化配置，仍统一使用数据库方言完成标识符转义。
                quoted_table = preparer.quote(table_name)
                connection.execute(text(f"DROP TABLE IF EXISTS {quoted_table}"))
