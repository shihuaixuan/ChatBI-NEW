"""Access Control 模型所有权与路由兼容契约测试。"""

from apps.access_control.models import UserModel, UserWsModel, WorkspaceModel
from apps.api import api_router
from apps.system.models.system_model import (
    UserWsModel as LegacyUserWsModel,
)
from apps.system.models.system_model import (
    WorkspaceModel as LegacyWorkspaceModel,
)
from apps.system.models.user import UserModel as LegacyUserModel


def test_system_models_reexport_access_control_owned_orm() -> None:
    assert LegacyUserModel is UserModel
    assert LegacyWorkspaceModel is WorkspaceModel
    assert LegacyUserWsModel is UserWsModel
    assert UserModel.__module__ == "apps.access_control.models.orm.identity"
    assert WorkspaceModel.__module__ == "apps.access_control.models.orm.workspace"


def test_identity_database_constraints_match_domain_invariants() -> None:
    user_constraint_names = {
        constraint.name for constraint in UserModel.__table__.constraints
    }
    membership_constraint_names = {
        constraint.name for constraint in UserWsModel.__table__.constraints
    }

    assert "uq_sys_user_account" in user_constraint_names
    assert "uq_sys_user_ws_uid_oid" in membership_constraint_names


def test_existing_management_paths_are_owned_by_access_control_api() -> None:
    management_paths = {
        "/user/info",
        "/user/pager/{pageNum}/{pageSize}",
        "/user/ws/{oid}",
        "/system/workspace",
        "/system/workspace/uws",
    }
    routes = [route for route in api_router.routes if route.path in management_paths]

    assert {route.path for route in routes} == management_paths
    assert all(route.endpoint.__module__.startswith("apps.access_control.api") for route in routes)


def test_excel_paths_remain_in_system_compatibility_adapter() -> None:
    excel_paths = {"/user/template", "/user/batchImport", "/user/errorRecord/{file_id}"}
    routes = [route for route in api_router.routes if route.path in excel_paths]

    assert {route.path for route in routes} == excel_paths
    assert all(route.endpoint.__module__ == "apps.system.api.user" for route in routes)
