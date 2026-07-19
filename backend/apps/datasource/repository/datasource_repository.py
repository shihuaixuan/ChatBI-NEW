from typing import Protocol

from apps.datasource.models.dto import DatasourceConnection, DatasourceRecord


class DatasourceRepository(Protocol):
    """数据源基本信息和本地物理元数据事务端口。"""

    def list_by_workspace(self, workspace_id: int) -> list[DatasourceRecord]: ...

    def get(self, datasource_id: int) -> DatasourceRecord | None: ...

    def name_exists(
        self,
        workspace_id: int,
        name: str,
        *,
        exclude_id: int | None = None,
    ) -> bool: ...

    def add_pending(self, datasource: DatasourceRecord) -> DatasourceRecord: ...

    def update(self, datasource: DatasourceRecord) -> DatasourceRecord: ...

    def delete(self, datasource_id: int) -> None: ...

    def rollback(self) -> None: ...


class DatasourceConnectionRepository(Protocol):
    """读取连接执行所需最小快照的仓储端口。"""

    def get_connection(self, datasource_id: int) -> DatasourceConnection | None: ...
