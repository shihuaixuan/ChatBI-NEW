from typing import Protocol

from apps.datasource.models.dto import DatasourceRecord


class DatasourceMaintenanceGateway(Protocol):
    """数据源类型解析和外部资源清理端口。"""

    def get_type_name(self, datasource_type: str) -> str: ...

    def cleanup(self, datasource: DatasourceRecord) -> None: ...
