"""行列权限持久化事实仓储端口。"""

from typing import Protocol

from apps.access_control.models.dto import StoredDataPermission, StoredDataRule


class DataPolicyRepository(Protocol):
    def list_permissions(
        self,
        datasource_id: int,
        table_ids: set[int],
    ) -> list[StoredDataPermission]: ...

    def list_rules(self, workspace_id: int) -> list[StoredDataRule]: ...
