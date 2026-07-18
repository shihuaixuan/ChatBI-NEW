"""Access Control 统一授权规则测试。"""

import asyncio
from collections.abc import Collection

import pytest

from apps.access_control.errors import (
    InvalidResourceReferenceError,
    ResourceContextRequiredError,
    ResourcePermissionDeniedError,
    SystemAdminRequiredError,
    UnsupportedPermissionRoleError,
    UnsupportedResourceTypeError,
    WorkspaceAdminRequiredError,
)
from apps.access_control.models.dto import (
    AuthorizationRequirement,
    AuthorizationSubject,
)
from apps.access_control.repository import (
    CompositeWorkspaceResourceScopeRepository,
    ResourceId,
)
from apps.access_control.services import AuthorizationService


class FakeResourceScopeRepository:
    def __init__(self, allowed_ids: Collection[ResourceId]) -> None:
        self.allowed_ids = allowed_ids
        self.calls: list[tuple[int, str]] = []

    async def list_resource_ids(
        self,
        workspace_id: int,
        resource_type: str,
    ) -> Collection[ResourceId]:
        self.calls.append((workspace_id, resource_type))
        return self.allowed_ids


class FakeResourceScopeReader:
    async def list_resource_ids(self, workspace_id: int) -> Collection[ResourceId]:
        return [workspace_id]


def _subject(
    *,
    workspace_id: int = 7,
    is_system_admin: bool = False,
    workspace_weight: int = 0,
) -> AuthorizationSubject:
    return AuthorizationSubject(
        workspace_id=workspace_id,
        is_system_admin=is_system_admin,
        workspace_weight=workspace_weight,
    )


def _requirement(
    *,
    roles: frozenset[str] = frozenset(),
    resource_type: str | None = None,
    resource_expression: str | None = None,
) -> AuthorizationRequirement:
    return AuthorizationRequirement(
        roles=roles,
        resource_type=resource_type,
        resource_expression=resource_expression,
    )


def test_system_admin_role_requires_system_admin() -> None:
    service = AuthorizationService(FakeResourceScopeRepository([]))

    with pytest.raises(SystemAdminRequiredError):
        asyncio.run(
            service.authorize(
                _subject(),
                _requirement(roles=frozenset({"admin"})),
            )
        )

    asyncio.run(
        service.authorize(
            _subject(is_system_admin=True),
            _requirement(roles=frozenset({"admin"})),
        )
    )


def test_workspace_admin_role_accepts_weight_or_system_admin() -> None:
    service = AuthorizationService(FakeResourceScopeRepository([]))
    requirement = _requirement(roles=frozenset({"ws_admin"}))

    with pytest.raises(WorkspaceAdminRequiredError):
        asyncio.run(service.authorize(_subject(), requirement))

    asyncio.run(service.authorize(_subject(workspace_weight=1), requirement))
    asyncio.run(service.authorize(_subject(is_system_admin=True), requirement))


def test_unknown_role_is_rejected_explicitly() -> None:
    service = AuthorizationService(FakeResourceScopeRepository([]))

    with pytest.raises(UnsupportedPermissionRoleError):
        asyncio.run(
            service.authorize(
                _subject(),
                _requirement(roles=frozenset({"owner"})),
            )
        )


def test_resource_ids_must_be_subset_of_workspace_scope() -> None:
    repository = FakeResourceScopeRepository([10, 11, 12])
    service = AuthorizationService(repository)
    requirement = _requirement(
        resource_type="ds",
        resource_expression="ids",
    )

    asyncio.run(service.authorize(_subject(), requirement, [10, 12]))
    assert repository.calls == [(7, "ds")]

    with pytest.raises(ResourcePermissionDeniedError):
        asyncio.run(service.authorize(_subject(), requirement, [10, 13]))


def test_empty_batch_is_allowed_without_scope_query() -> None:
    repository = FakeResourceScopeRepository([10])
    service = AuthorizationService(repository)

    asyncio.run(
        service.authorize(
            _subject(),
            _requirement(resource_type="ds", resource_expression="ids"),
            [],
        )
    )

    assert repository.calls == []


def test_missing_or_invalid_resource_never_silently_passes() -> None:
    service = AuthorizationService(FakeResourceScopeRepository([10]))
    requirement = _requirement(
        resource_type="ds",
        resource_expression="id",
    )

    with pytest.raises(ResourceContextRequiredError):
        asyncio.run(service.authorize(_subject(), requirement))
    with pytest.raises(InvalidResourceReferenceError):
        asyncio.run(service.authorize(_subject(), requirement, None))
    with pytest.raises(InvalidResourceReferenceError):
        asyncio.run(service.authorize(_subject(), requirement, ""))


def test_resource_type_without_expression_is_invalid() -> None:
    service = AuthorizationService(FakeResourceScopeRepository([10]))

    with pytest.raises(ResourceContextRequiredError):
        asyncio.run(
            service.authorize(
                _subject(),
                _requirement(resource_type="ds"),
            )
        )


def test_composite_scope_repository_rejects_unknown_type() -> None:
    repository = CompositeWorkspaceResourceScopeRepository(
        {"chat": FakeResourceScopeReader()}
    )

    assert asyncio.run(repository.list_resource_ids(7, "chat")) == [7]
    with pytest.raises(UnsupportedResourceTypeError):
        asyncio.run(repository.list_resource_ids(7, "dashboard"))
