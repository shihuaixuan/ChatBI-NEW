"""权限变量仓储端口。"""

from typing import Protocol

from apps.access_control.models.dto import (
    AccessVariableCreateData,
    AccessVariableRecord,
    AccessVariableUpdateData,
)
from common.core.schemas import PaginatedResponse


class AccessVariableRepository(Protocol):
    def get(self, variable_id: int) -> AccessVariableRecord | None: ...

    def get_by_name(
        self,
        name: str,
        *,
        exclude_id: int | None = None,
    ) -> AccessVariableRecord | None: ...

    def list_by_ids(self, variable_ids: set[int]) -> list[AccessVariableRecord]: ...

    def list_all(self, keyword: str | None = None) -> list[AccessVariableRecord]: ...

    async def list_page(
        self,
        *,
        page: int,
        size: int,
        keyword: str | None,
    ) -> PaginatedResponse[AccessVariableRecord]: ...

    def create(self, data: AccessVariableCreateData) -> AccessVariableRecord: ...

    def update(
        self,
        variable_id: int,
        data: AccessVariableUpdateData,
    ) -> AccessVariableRecord | None: ...

    def delete(self, variable_ids: list[int]) -> list[int]: ...
