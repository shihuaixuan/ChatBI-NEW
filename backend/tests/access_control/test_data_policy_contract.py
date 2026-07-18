"""数据策略所有权、路由和数据库约束契约测试。"""

from fastapi.routing import APIRoute

from apps.access_control.models.orm import AccessVariableModel
from apps.api import api_router
from apps.system.models.system_variable_model import SystemVariable


def test_system_reexports_access_control_variable_model() -> None:
    assert SystemVariable is AccessVariableModel
    assert AccessVariableModel.__module__ == (
        "apps.access_control.models.orm.access_variable"
    )


def test_variable_routes_are_owned_by_access_control() -> None:
    routes = [
        route
        for route in api_router.routes
        if isinstance(route, APIRoute) and route.path.startswith("/sys_variable")
    ]

    assert len(routes) == 4
    assert all(
        route.endpoint.__module__ == "apps.access_control.api.access_variable"
        for route in routes
    )


def test_access_variable_constraints_match_domain_invariants() -> None:
    constraint_names = {
        constraint.name for constraint in AccessVariableModel.__table__.constraints
    }
    index_names = {index.name for index in AccessVariableModel.__table__.indexes}

    assert "ck_system_variable_type" in constraint_names
    assert "ck_system_variable_var_type" in constraint_names
    assert "ck_system_variable_name_not_blank" in constraint_names
    assert "ck_system_variable_value_array" in constraint_names
    assert "ck_system_variable_custom_creator" in constraint_names
    assert "uq_system_variable_name" in constraint_names
    assert "ix_system_variable_type_name" in index_names
    assert AccessVariableModel.__table__.c.value.nullable is False
