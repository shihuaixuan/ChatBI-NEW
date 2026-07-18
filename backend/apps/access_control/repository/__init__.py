"""Access Control 仓储端口与实现导出。"""

from apps.access_control.repository.identity_workspace_repository import (
    IdentityWorkspaceRepository,
)
from apps.access_control.repository.resource_scope import (
    CompositeWorkspaceResourceScopeRepository,
    ResourceId,
    WorkspaceResourceScopeReader,
    WorkspaceResourceScopeRepository,
)

__all__ = [
    "CompositeWorkspaceResourceScopeRepository",
    "ResourceId",
    "WorkspaceResourceScopeReader",
    "WorkspaceResourceScopeRepository",
    "IdentityWorkspaceRepository",
]
