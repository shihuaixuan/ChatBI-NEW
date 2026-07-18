"""工作空间资源范围仓储端口。"""

from collections.abc import Collection, Mapping
from typing import Protocol, TypeAlias

from apps.access_control.errors import UnsupportedResourceTypeError

ResourceId: TypeAlias = int | str


class WorkspaceResourceScopeReader(Protocol):
    """单一资源领域提供的工作空间范围读取契约。"""

    async def list_resource_ids(self, workspace_id: int) -> Collection[ResourceId]: ...


class WorkspaceResourceScopeRepository(Protocol):
    """授权 Service 使用的统一资源范围端口。"""

    async def list_resource_ids(
        self,
        workspace_id: int,
        resource_type: str,
    ) -> Collection[ResourceId]: ...


class CompositeWorkspaceResourceScopeRepository:
    """按资源类型转发到资源所属领域。"""

    def __init__(
        self,
        readers: Mapping[str, WorkspaceResourceScopeReader],
    ) -> None:
        self._readers = dict(readers)

    async def list_resource_ids(
        self,
        workspace_id: int,
        resource_type: str,
    ) -> Collection[ResourceId]:
        reader = self._readers.get(resource_type)
        if reader is None:
            raise UnsupportedResourceTypeError(resource_type)
        return await reader.list_resource_ids(workspace_id)

