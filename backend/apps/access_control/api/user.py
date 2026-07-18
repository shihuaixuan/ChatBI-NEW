"""用户管理接口。"""


from fastapi import APIRouter, Path, Query

from apps.access_control.cache import clear_user_cache
from apps.access_control.composition import build_identity_workspace_service
from apps.access_control.errors import AccessControlError
from apps.access_control.http_error_mapping import raise_identity_http_error
from apps.access_control.identity import user_ws_options
from apps.access_control.models.dto import (
    PwdEditor,
    UserCreator,
    UserEditor,
    UserGrid,
    UserInfoDTO,
    UserLanguage,
    UserStatus,
    UserWs,
)
from apps.access_control.permission import SqlbotPermission, require_permissions
from apps.swagger.i18n import PLACEHOLDER_PREFIX
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.config import settings
from common.core.deps import CurrentUser, SessionDep, Trans
from common.core.schemas import PaginatedResponse

router = APIRouter(tags=["system_user"], prefix="/user")


async def create(session: SessionDep, creator: UserCreator, trans: Trans):
    """创建用户的公开接口适配入口，同时供 XPack 兼容调用。"""
    try:
        return build_identity_workspace_service(session).create_user(creator)
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)


@router.get(
    "/info",
    summary=f"{PLACEHOLDER_PREFIX}system_user_current_user",
    description=f"{PLACEHOLDER_PREFIX}system_user_current_user_desc",
)
async def user_info(current_user: CurrentUser) -> UserInfoDTO:
    return current_user


@router.get("/defaultPwd", include_in_schema=False)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def default_pwd() -> str:
    return settings.DEFAULT_PWD


@router.get(
    "/pager/{pageNum}/{pageSize}",
    response_model=PaginatedResponse[UserGrid],
    summary=f"{PLACEHOLDER_PREFIX}system_user_grid",
    description=f"{PLACEHOLDER_PREFIX}system_user_grid",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def pager(
    session: SessionDep,
    pageNum: int = Path(
        ...,
        title=f"{PLACEHOLDER_PREFIX}page_num",
        description=f"{PLACEHOLDER_PREFIX}page_num",
    ),
    pageSize: int = Path(
        ...,
        title=f"{PLACEHOLDER_PREFIX}page_size",
        description=f"{PLACEHOLDER_PREFIX}page_size",
    ),
    keyword: str | None = Query(None, description=f"{PLACEHOLDER_PREFIX}keyword"),
    status: int | None = Query(None, description=f"{PLACEHOLDER_PREFIX}status"),
    origins: list[int] | None = Query(
        None,
        description=f"{PLACEHOLDER_PREFIX}origin",
    ),
    oidlist: list[int] | None = Query(
        None,
        description=f"{PLACEHOLDER_PREFIX}oid",
    ),
) -> PaginatedResponse[UserGrid]:
    return await build_identity_workspace_service(session).list_users(
        page=pageNum,
        size=pageSize,
        keyword=keyword,
        status=status,
        origins=origins,
        workspace_ids=oidlist,
    )


@router.get("/ws", include_in_schema=False)
async def ws_options(
    session: SessionDep,
    current_user: CurrentUser,
    trans: Trans,
) -> list[UserWs]:
    return await user_ws_options(session, current_user.id, trans)


@router.put(
    "/ws/{oid}",
    summary=f"{PLACEHOLDER_PREFIX}switch_oid_api",
    description=f"{PLACEHOLDER_PREFIX}switch_oid_api",
)
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.USER,
        resource_id_expr="current_user.id",
    )
)
async def ws_change(
    session: SessionDep,
    current_user: CurrentUser,
    trans: Trans,
    oid: int = Path(description=f"{PLACEHOLDER_PREFIX}oid"),
) -> None:
    try:
        build_identity_workspace_service(session).switch_workspace(current_user.id, oid)
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    await clear_user_cache(current_user.id)


@router.post(
    "",
    summary=f"{PLACEHOLDER_PREFIX}user_create_api",
    description=f"{PLACEHOLDER_PREFIX}user_create_api",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.CREATE,
        module=OperationModules.USER,
        result_id_expr="id",
    )
)
async def user_create(
    session: SessionDep,
    creator: UserCreator,
    trans: Trans,
):
    return await create(session, creator, trans)


@router.put(
    "",
    summary=f"{PLACEHOLDER_PREFIX}user_update_api",
    description=f"{PLACEHOLDER_PREFIX}user_update_api",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.USER,
        resource_id_expr="editor.id",
    )
)
async def update(
    session: SessionDep,
    editor: UserEditor,
    trans: Trans,
) -> None:
    try:
        build_identity_workspace_service(session).update_user(editor)
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    await clear_user_cache(editor.id)


@router.delete(
    "/{id}",
    summary=f"{PLACEHOLDER_PREFIX}user_del_api",
    description=f"{PLACEHOLDER_PREFIX}user_del_api",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.DELETE,
        module=OperationModules.USER,
        resource_id_expr="id",
    )
)
async def delete(
    session: SessionDep,
    trans: Trans,
    id: int = Path(description=f"{PLACEHOLDER_PREFIX}uid"),
) -> None:
    try:
        deleted_ids = build_identity_workspace_service(session).delete_users([id])
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    for user_id in deleted_ids:
        await clear_user_cache(user_id)


@router.delete(
    "",
    summary=f"{PLACEHOLDER_PREFIX}user_batchdel_api",
    description=f"{PLACEHOLDER_PREFIX}user_batchdel_api",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.DELETE,
        module=OperationModules.USER,
        resource_id_expr="id_list",
    )
)
async def batch_del(session: SessionDep, trans: Trans, id_list: list[int]) -> None:
    try:
        deleted_ids = build_identity_workspace_service(session).delete_users(id_list)
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    for user_id in deleted_ids:
        await clear_user_cache(user_id)


@router.put(
    "/language",
    summary=f"{PLACEHOLDER_PREFIX}language_change",
    description=f"{PLACEHOLDER_PREFIX}language_change",
)
async def lang_change(
    session: SessionDep,
    current_user: CurrentUser,
    trans: Trans,
    language: UserLanguage,
) -> None:
    try:
        build_identity_workspace_service(session).update_language(
            current_user.id,
            language.language,
        )
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    await clear_user_cache(current_user.id)


@router.patch(
    "/pwd/{id}",
    summary=f"{PLACEHOLDER_PREFIX}reset_pwd",
    description=f"{PLACEHOLDER_PREFIX}reset_pwd",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.RESET_PWD,
        module=OperationModules.USER,
        resource_id_expr="id",
    )
)
async def pwd_reset(
    session: SessionDep,
    trans: Trans,
    id: int = Path(description=f"{PLACEHOLDER_PREFIX}uid"),
) -> None:
    try:
        build_identity_workspace_service(session).reset_password(id)
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    await clear_user_cache(id)


@router.put(
    "/pwd",
    summary=f"{PLACEHOLDER_PREFIX}update_pwd",
    description=f"{PLACEHOLDER_PREFIX}update_pwd",
)
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE_PWD,
        module=OperationModules.USER,
        result_id_expr="id",
    )
)
async def pwd_update(
    session: SessionDep,
    current_user: CurrentUser,
    trans: Trans,
    editor: PwdEditor,
):
    try:
        updated = build_identity_workspace_service(session).update_password(
            current_user.id,
            editor.pwd,
            editor.new_pwd,
        )
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    await clear_user_cache(current_user.id)
    return updated


@router.patch(
    "/status",
    summary=f"{PLACEHOLDER_PREFIX}update_status",
    description=f"{PLACEHOLDER_PREFIX}update_status",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE_STATUS,
        module=OperationModules.USER,
        resource_id_expr="statusDto.id",
    )
)
async def status_change(
    session: SessionDep,
    trans: Trans,
    statusDto: UserStatus,
):
    # 保持现有接口对不支持状态的响应契约。
    if statusDto.status not in (0, 1):
        return {"message": "status not supported"}
    try:
        build_identity_workspace_service(session).update_status(
            statusDto.id,
            statusDto.status,
        )
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    await clear_user_cache(statusDto.id)
    return None


@router.get(
    "/{id}",
    response_model=UserEditor,
    summary=f"{PLACEHOLDER_PREFIX}user_detail_api",
    description=f"{PLACEHOLDER_PREFIX}user_detail_api",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def query(
    session: SessionDep,
    trans: Trans,
    id: int = Path(description=f"{PLACEHOLDER_PREFIX}uid"),
) -> UserEditor:
    try:
        return build_identity_workspace_service(session).get_user_detail(id)
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
