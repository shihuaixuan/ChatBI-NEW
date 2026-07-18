"""授权接口的公开兼容入口。"""

from apps.access_control.api.permission import (
    require_permissions,
    resolve_resource_reference,
)
from apps.access_control.api.request_context import (
    RequestContext,
    RequestContextMiddleware,
)
from apps.access_control.models.dto import SqlbotPermission

__all__ = [
    "RequestContext",
    "RequestContextMiddleware",
    "SqlbotPermission",
    "require_permissions",
    "resolve_resource_reference",
]

