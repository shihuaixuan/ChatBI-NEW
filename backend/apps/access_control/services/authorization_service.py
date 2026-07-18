"""统一角色与资源授权 Service。"""

from typing import Final

from apps.access_control.errors import (
    InvalidResourceReferenceError,
    ResourceContextRequiredError,
    ResourcePermissionDeniedError,
    SystemAdminRequiredError,
    UnsupportedPermissionRoleError,
    WorkspaceAdminRequiredError,
)
from apps.access_control.models.dto import (
    AuthorizationRequirement,
    AuthorizationSubject,
)
from apps.access_control.repository import (
    ResourceId,
    WorkspaceResourceScopeRepository,
)

SYSTEM_ADMIN_ROLE: Final = "admin"
WORKSPACE_ADMIN_ROLE: Final = "ws_admin"
SUPPORTED_ROLES: Final = frozenset({SYSTEM_ADMIN_ROLE, WORKSPACE_ADMIN_ROLE})
_RESOURCE_NOT_PROVIDED: Final = object()


class AuthorizationService:
    """集中表达角色和工作空间资源范围不变量。"""

    def __init__(
        self,
        resource_scope_repository: WorkspaceResourceScopeRepository,
    ) -> None:
        self._resource_scope_repository = resource_scope_repository

    async def authorize(
        self,
        subject: AuthorizationSubject,
        requirement: AuthorizationRequirement,
        resource: object = _RESOURCE_NOT_PROVIDED,
    ) -> None:
        # 保留现有语义：系统管理员在没有资源约束时直接通过。
        if subject.is_system_admin and requirement.resource_type is None:
            return

        unsupported_roles = requirement.roles - SUPPORTED_ROLES
        if unsupported_roles:
            raise UnsupportedPermissionRoleError(sorted(unsupported_roles)[0])
        if SYSTEM_ADMIN_ROLE in requirement.roles and not subject.is_system_admin:
            raise SystemAdminRequiredError()
        if (
            WORKSPACE_ADMIN_ROLE in requirement.roles
            and subject.workspace_weight == 0
            and not subject.is_system_admin
        ):
            raise WorkspaceAdminRequiredError()

        if requirement.resource_type is None:
            return
        if requirement.resource_expression is None or resource is _RESOURCE_NOT_PROVIDED:
            raise ResourceContextRequiredError()
        if resource is None:
            raise InvalidResourceReferenceError()

        if isinstance(resource, (list, tuple, set, frozenset)):
            requested_ids = set(resource)
        else:
            requested_ids = {resource}

        # 空批量操作没有目标资源，保持原接口的通过语义。
        if not requested_ids:
            return
        if any(
            isinstance(resource_id, bool)
            or not isinstance(resource_id, (int, str))
            or resource_id == ""
            for resource_id in requested_ids
        ):
            raise InvalidResourceReferenceError()

        allowed_ids: set[ResourceId] = set(
            await self._resource_scope_repository.list_resource_ids(
                subject.workspace_id,
                requirement.resource_type,
            )
        )
        if not requested_ids.issubset(allowed_ids):
            raise ResourcePermissionDeniedError()
