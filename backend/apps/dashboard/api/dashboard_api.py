from fastapi import APIRouter, HTTPException

from apps.dashboard.composition import build_dashboard_service
from apps.dashboard.models import BaseDashboard, CreateDashboard, QueryDashboard
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.deps import CurrentUser, SessionDep
from common.interfaces.i18n import PLACEHOLDER_PREFIX

router = APIRouter(tags=["Dashboard"], prefix="/dashboard")


@router.post("/list_resource", summary=f"{PLACEHOLDER_PREFIX}list_resource_api")
async def list_resource_api(
    session: SessionDep,
    dashboard: QueryDashboard,
    current_user: CurrentUser,
):
    return build_dashboard_service(session).list_resource(dashboard, current_user)


@router.post("/load_resource", summary=f"{PLACEHOLDER_PREFIX}load_resource_api")
async def load_resource_api(
    session: SessionDep,
    current_user: CurrentUser,
    dashboard: QueryDashboard,
):
    try:
        return build_dashboard_service(session).load_resource(
            dashboard,
            current_user,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this resource",
        ) from exc


@router.post(
    "/create_resource",
    response_model=BaseDashboard,
    summary=f"{PLACEHOLDER_PREFIX}create_resource_api",
)
async def create_resource_api(
    session: SessionDep,
    user: CurrentUser,
    dashboard: CreateDashboard,
):
    return build_dashboard_service(session).create_resource(user, dashboard)


@router.post(
    "/update_resource",
    response_model=BaseDashboard,
    summary=f"{PLACEHOLDER_PREFIX}update_resource",
)
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.DASHBOARD,
        resource_id_expr="dashboard.id",
    )
)
async def update_resource_api(
    session: SessionDep,
    user: CurrentUser,
    dashboard: QueryDashboard,
):
    return build_dashboard_service(session).update_resource(user, dashboard)


@router.delete(
    "/delete_resource/{resource_id}/{name}",
    summary=f"{PLACEHOLDER_PREFIX}delete_resource_api",
)
@system_log(
    LogConfig(
        operation_type=OperationType.DELETE,
        module=OperationModules.DASHBOARD,
        resource_id_expr="resource_id",
        remark_expr="name",
    )
)
async def delete_resource_api(
    session: SessionDep,
    current_user: CurrentUser,
    resource_id: str,
    name: str,  # noqa: ARG001 - 路径名称由审计装饰器读取
):
    return build_dashboard_service(session).delete_resource(
        current_user, resource_id
    )


@router.post(
    "/create_canvas",
    response_model=BaseDashboard,
    summary=f"{PLACEHOLDER_PREFIX}create_canvas_api",
)
@system_log(
    LogConfig(
        operation_type=OperationType.CREATE,
        module=OperationModules.DASHBOARD,
        result_id_expr="id",
    )
)
async def create_canvas_api(
    session: SessionDep,
    user: CurrentUser,
    dashboard: CreateDashboard,
):
    return build_dashboard_service(session).create_canvas(user, dashboard)


@router.post(
    "/update_canvas",
    response_model=BaseDashboard,
    summary=f"{PLACEHOLDER_PREFIX}update_canvas_api",
)
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.DASHBOARD,
        resource_id_expr="dashboard.id",
    )
)
async def update_canvas_api(
    session: SessionDep,
    user: CurrentUser,
    dashboard: CreateDashboard,
):
    return build_dashboard_service(session).update_canvas(user, dashboard)


@router.post("/check_name", summary=f"{PLACEHOLDER_PREFIX}check_name_api")
async def check_name_api(
    session: SessionDep,
    user: CurrentUser,
    dashboard: QueryDashboard,
):
    return build_dashboard_service(session).validate_name(user, dashboard)
