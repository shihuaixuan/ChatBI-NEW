"""旧 System 授权导入路径兼容层。"""

from apps.access_control.permission import (
    RequestContext,
    RequestContextMiddleware,
    SqlbotPermission,
    require_permissions,
    resolve_resource_reference,
)

__all__ = [
    "RequestContext",
    "RequestContextMiddleware",
    "SqlbotPermission",
    "require_permissions",
    "resolve_resource_reference",
]
