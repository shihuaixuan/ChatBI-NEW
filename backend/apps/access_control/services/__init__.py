"""Access Control Service 稳定导出。"""

from apps.access_control.services.authorization_service import AuthorizationService
from apps.access_control.services.identity_workspace_service import (
    IdentityWorkspaceService,
)

__all__ = ["AuthorizationService", "IdentityWorkspaceService"]
