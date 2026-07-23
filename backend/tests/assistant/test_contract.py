"""Assistant 所有权、接口和数据库约束契约测试。"""

from fastapi.routing import APIRoute

from apps.api import api_router
from apps.assistant import (
    AssistantModel,
    AssistantPublicInfo,
)


def test_assistant_models_are_owned_by_assistant_domain() -> None:
    assert AssistantModel.__module__ == "apps.assistant.models.orm.assistant"


def test_assistant_routes_are_owned_by_assistant_domain() -> None:
    routes = [
        route
        for route in api_router.routes
        if isinstance(route, APIRoute) and route.path.startswith("/system/assistant")
    ]

    assert len(routes) == 12
    assert all(
        route.endpoint.__module__ == "apps.assistant.api.assistants" for route in routes
    )


def test_page_embedded_routes_are_owned_by_assistant_domain() -> None:
    paths = {
        route.path
        for route in api_router.routes
        if route.path.startswith("/system/embedded")
    }

    assert paths == {
        "/system/embedded",
        "/system/embedded/{id}",
        "/system/embedded/{page_num}/{page_size}",
        "/system/embedded/secret/{id}",
    }


def test_public_assistant_contract_does_not_expose_app_secret() -> None:
    assert "app_secret" not in AssistantPublicInfo.model_fields


def test_assistant_database_constraints_match_domain_invariants() -> None:
    constraint_names = {
        constraint.name for constraint in AssistantModel.__table__.constraints
    }
    index_names = {index.name for index in AssistantModel.__table__.indexes}

    assert "ck_sys_assistant_type" in constraint_names
    assert "uq_sys_assistant_app_id" in constraint_names
    assert "uq_sys_assistant_app_secret" in constraint_names
    assert "ix_sys_assistant_oid_type" in index_names
    assert AssistantModel.__table__.c.oid.nullable is False
