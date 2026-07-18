"""认证与 API Key 所有权及路由契约测试。"""

from apps.access_control.models import ApiKeyModel, AuthenticationModel
from apps.api import api_router
from apps.system.models.system_model import ApiKeyModel as LegacyApiKeyModel
from apps.system.models.system_model import (
    AuthenticationModel as LegacyAuthenticationModel,
)


def test_system_reexports_access_control_authentication_models() -> None:
    assert LegacyApiKeyModel is ApiKeyModel
    assert LegacyAuthenticationModel is AuthenticationModel
    assert ApiKeyModel.__module__ == "apps.access_control.models.orm.api_key"
    assert AuthenticationModel.__module__ == (
        "apps.access_control.models.orm.authentication"
    )


def test_api_key_constraints_match_domain_invariants() -> None:
    constraint_names = {
        constraint.name for constraint in ApiKeyModel.__table__.constraints
    }
    index_names = {index.name for index in ApiKeyModel.__table__.indexes}

    assert "uq_sys_apikey_access_key" in constraint_names
    assert "ix_sys_apikey_uid" in index_names


def test_login_and_api_key_routes_are_owned_by_access_control() -> None:
    expected_operations = {
        ("/login/access-token", "POST"),
        ("/login/logout", "POST"),
        ("/system/apikey", "GET"),
        ("/system/apikey", "POST"),
        ("/system/apikey/status", "PUT"),
        ("/system/apikey/{id}", "DELETE"),
    }
    operations = {
        (route.path, method): route.endpoint.__module__
        for route in api_router.routes
        for method in route.methods or set()
        if (route.path, method) in expected_operations
    }

    assert set(operations) == expected_operations
    assert all(
        module.startswith("apps.access_control.api")
        for module in operations.values()
    )
