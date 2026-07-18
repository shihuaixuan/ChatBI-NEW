"""API Key 管理接口。"""

from fastapi import APIRouter

from apps.access_control.cache import clear_api_key_cache
from apps.access_control.composition import build_api_key_service
from apps.access_control.errors import AccessControlError
from apps.access_control.http_error_mapping import raise_api_key_http_error
from apps.access_control.models.dto import ApiKeyGridItem, ApiKeyStatus
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.deps import CurrentUser, SessionDep
from common.utils.time import get_timestamp

router = APIRouter(
    tags=["system_apikey"],
    prefix="/system/apikey",
    include_in_schema=False,
)


@router.get("")
async def grid(
    session: SessionDep,
    current_user: CurrentUser,
) -> list[ApiKeyGridItem]:
    return build_api_key_service(session).list_api_keys(current_user.id)


@router.post("")
@system_log(
    LogConfig(
        operation_type=OperationType.CREATE,
        module=OperationModules.API_KEY,
        result_id_expr="result.self",
    )
)
async def create(session: SessionDep, current_user: CurrentUser) -> int:
    try:
        api_key = build_api_key_service(session).create_api_key(
            current_user.id,
            get_timestamp(),
        )
    except AccessControlError as exc:
        raise_api_key_http_error(exc)
    return api_key.id


@router.put("/status")
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.API_KEY,
        resource_id_expr="dto.id",
    )
)
async def status(
    session: SessionDep,
    current_user: CurrentUser,
    dto: ApiKeyStatus,
) -> None:
    try:
        api_key = build_api_key_service(session).update_status(
            user_id=current_user.id,
            api_key_id=dto.id,
            status=dto.status,
        )
    except AccessControlError as exc:
        raise_api_key_http_error(exc)
    await clear_api_key_cache(api_key.access_key)


@router.delete("/{id}")
@system_log(
    LogConfig(
        operation_type=OperationType.DELETE,
        module=OperationModules.API_KEY,
        resource_id_expr="id",
    )
)
async def delete(
    session: SessionDep,
    current_user: CurrentUser,
    id: int,
) -> None:
    try:
        api_key = build_api_key_service(session).delete_api_key(
            user_id=current_user.id,
            api_key_id=id,
        )
    except AccessControlError as exc:
        raise_api_key_http_error(exc)
    await clear_api_key_cache(api_key.access_key)
