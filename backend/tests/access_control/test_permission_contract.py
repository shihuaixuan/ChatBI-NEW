"""授权装饰器参数解析和兼容入口测试。"""

import asyncio
from inspect import signature
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Request

import apps.access_control.api.permission as permission_module
from apps.access_control.errors import ResourceContextRequiredError
from apps.access_control.models.dto import (
    AuthorizationRequirement,
    AuthorizationSubject,
)
from apps.access_control.permission import (
    RequestContext,
    SqlbotPermission,
    require_permissions,
    resolve_resource_reference,
)


class RecordingAuthorizationService:
    def __init__(self) -> None:
        self.calls: list[
            tuple[AuthorizationSubject, AuthorizationRequirement, object]
        ] = []

    async def authorize(
        self,
        subject: AuthorizationSubject,
        requirement: AuthorizationRequirement,
        resource: object,
    ) -> None:
        self.calls.append((subject, requirement, resource))


def _request_with_user(user: object) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "state": {},
        }
    )
    request.state.current_user = user
    return request


def test_resource_reference_supports_named_nested_and_positional_values() -> None:
    def endpoint(id: int, payload: object) -> None:
        _ = id, payload
        return None

    payload = SimpleNamespace(datasource=21)
    assert resolve_resource_reference(endpoint, (10, payload), {}, "id") == 10
    assert (
        resolve_resource_reference(
            endpoint,
            (10, payload),
            {},
            "payload.datasource",
        )
        == 21
    )
    assert resolve_resource_reference(endpoint, (10, payload), {}, "args[1]") is payload


def test_resource_reference_rejects_missing_root_or_attribute() -> None:
    def endpoint(id: int) -> None:
        _ = id
        return None

    with pytest.raises(ResourceContextRequiredError):
        resolve_resource_reference(endpoint, (), {}, "id")
    with pytest.raises(ResourceContextRequiredError):
        resolve_resource_reference(endpoint, (1,), {}, "id.value")
    with pytest.raises(ResourceContextRequiredError):
        resolve_resource_reference(endpoint, (1,), {}, "args[2]")


def test_decorator_delegates_to_authorization_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingAuthorizationService()
    monkeypatch.setattr(permission_module, "_authorization_service", service)

    @require_permissions(
        SqlbotPermission(type="ds", keyExpression="payload.datasource")
    )
    async def endpoint(payload: Any) -> str:
        return "ok" if payload.datasource else "invalid"

    request = _request_with_user(
        SimpleNamespace(oid=7, weight=1, isAdmin=False)
    )

    async def invoke_endpoint() -> str:
        return await endpoint(SimpleNamespace(datasource=21))

    token = RequestContext.set_request(request)
    try:
        assert asyncio.run(invoke_endpoint()) == "ok"
    finally:
        RequestContext.reset(token)

    assert list(signature(endpoint).parameters) == ["payload"]
    assert service.calls == [
        (
            AuthorizationSubject(
                workspace_id=7,
                is_system_admin=False,
                workspace_weight=1,
            ),
            AuthorizationRequirement(
                roles=frozenset(),
                resource_type="ds",
                resource_expression="payload.datasource",
            ),
            21,
        )
    ]
