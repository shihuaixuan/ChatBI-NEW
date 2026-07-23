"""旧 Graph 权限适配器兼容入口。"""

from apps.chatbi.services.execution.sql_permission import (
    PermissionAdapter,
    SQLPermissionService,
)

__all__ = [
    "PermissionAdapter",
    "SQLPermissionService",
]
