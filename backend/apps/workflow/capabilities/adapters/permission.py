"""旧 Graph 权限适配器兼容入口。"""

from apps.chatbi.services.sql_permission import (
    PermissionAdapter,
    PermissionPolicyProvider,
    SQLPermissionService,
)

__all__ = [
    "PermissionAdapter",
    "PermissionPolicyProvider",
    "SQLPermissionService",
]
