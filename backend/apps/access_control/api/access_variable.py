"""权限变量管理接口。"""

from collections.abc import Callable

from fastapi import APIRouter

from apps.access_control.composition import build_access_variable_service
from apps.access_control.errors import AccessControlError
from apps.access_control.http_error_mapping import raise_variable_http_error
from apps.access_control.models.dto import (
    AccessVariableFilter,
    AccessVariableInput,
    AccessVariableRecord,
)
from apps.access_control.permission import SqlbotPermission, require_permissions
from common.interfaces.i18n import PLACEHOLDER_PREFIX
from common.core.deps import CurrentUser, SessionDep, Trans
from common.core.schemas import PaginatedResponse

router = APIRouter(tags=["System_variable"], prefix="/sys_variable")


def _localize(
    variable: AccessVariableRecord,
    trans: Callable[[str], str],
) -> AccessVariableRecord:
    if variable.type != "system":
        return variable
    return variable.model_copy(update={"name": trans(variable.name)})


@router.post(
    "/save",
    response_model=None,
    summary=f"{PLACEHOLDER_PREFIX}variable_save",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def save_variable(
    session: SessionDep,
    current_user: CurrentUser,
    trans: Trans,
    variable: AccessVariableInput,
) -> bool:
    try:
        build_access_variable_service(session).save(variable, current_user.id)
    except AccessControlError as exc:
        raise_variable_http_error(exc, trans)
    return True


@router.post(
    "/delete",
    response_model=None,
    summary=f"{PLACEHOLDER_PREFIX}variable_delete",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def delete_variable(
    session: SessionDep,
    trans: Trans,
    ids: list[int],
) -> None:
    try:
        build_access_variable_service(session).delete(ids)
    except AccessControlError as exc:
        raise_variable_http_error(exc, trans)


@router.post(
    "/listAll",
    response_model=None,
    summary=f"{PLACEHOLDER_PREFIX}variable_list",
)
async def list_all_data(
    session: SessionDep,
    trans: Trans,
    variable: AccessVariableFilter | None = None,
) -> list[AccessVariableRecord]:
    records = build_access_variable_service(session).list_all(
        variable.name if variable else None
    )
    return [_localize(record, trans) for record in records]


@router.post(
    "/listPage/{pageNum}/{pageSize}",
    response_model=None,
    summary=f"{PLACEHOLDER_PREFIX}variable_page",
)
async def pager(
    session: SessionDep,
    trans: Trans,
    pageNum: int,
    pageSize: int,
    variable: AccessVariableFilter | None = None,
) -> PaginatedResponse[AccessVariableRecord]:
    result = await build_access_variable_service(session).list_page(
        page=pageNum,
        size=pageSize,
        keyword=variable.name if variable else None,
    )
    result.items = [_localize(record, trans) for record in result.items]
    return result
