"""工作空间与成员管理接口。"""


from fastapi import APIRouter, Path, Query

from apps.access_control.cache import clear_user_cache
from apps.access_control.composition import build_identity_workspace_service
from apps.access_control.errors import AccessControlError
from apps.access_control.http_error_mapping import raise_identity_http_error
from apps.access_control.models.dto import (
    UserWsBase,
    UserWsDTO,
    UserWsEditor,
    UserWsOption,
    WorkspaceBase,
    WorkspaceEditor,
    WorkspaceRecord,
    WorkspaceUser,
)
from apps.access_control.permission import SqlbotPermission, require_permissions
from common.interfaces.i18n import PLACEHOLDER_PREFIX
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.deps import CurrentUser, SessionDep, Trans
from common.core.schemas import PaginatedResponse
from common.utils.time import get_timestamp

router = APIRouter(tags=["system_ws"], prefix="/system/workspace")


async def edit(
    session: SessionDep,
    trans: Trans,
    editor: UserWsEditor,
) -> None:
    """更新成员权重的公开接口适配入口，同时供 XPack 兼容调用。"""
    if not editor.oid or not editor.uid:
        raise Exception(trans("i18n_miss_args", key="[oid, uid]"))
    try:
        build_identity_workspace_service(session).update_member_weight(
            workspace_id=editor.oid,
            user_id=editor.uid,
            weight=editor.weight,
        )
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    await clear_user_cache(editor.uid)


@router.get(
    "/uws/option/pager/{pageNum}/{pageSize}",
    response_model=PaginatedResponse[UserWsOption],
    summary=f"{PLACEHOLDER_PREFIX}ws_user_grid_api",
    description=f"{PLACEHOLDER_PREFIX}ws_user_grid_api",
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def option_pager(
    session: SessionDep,
    current_user: CurrentUser,
    trans: Trans,
    pageNum: int = Path(description=f"{PLACEHOLDER_PREFIX}page_num"),
    pageSize: int = Path(description=f"{PLACEHOLDER_PREFIX}page_size"),
    oid: int = Query(description=f"{PLACEHOLDER_PREFIX}oid"),
    keyword: str | None = Query(None, description=f"{PLACEHOLDER_PREFIX}keyword"),
) -> PaginatedResponse[UserWsOption]:
    if not current_user.isAdmin:
        raise Exception(
            trans(
                "i18n_permission.no_permission",
                url=", ",
                msg=trans("i18n_permission.only_admin"),
            )
        )
    if not oid:
        raise Exception(trans("i18n_miss_args", key="[oid]"))
    try:
        return await build_identity_workspace_service(session).list_available_users(
            workspace_id=oid,
            page=pageNum,
            size=pageSize,
            keyword=keyword,
        )
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)


@router.get(
    "/uws/option",
    response_model=UserWsOption | None,
    include_in_schema=False,
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def option_user(
    session: SessionDep,
    current_user: CurrentUser,
    trans: Trans,
    keyword: str = Query(description="搜索关键字"),
) -> UserWsOption | None:
    if not keyword:
        raise Exception(trans("i18n_miss_args", key="[keyword]"))
    if not current_user.isAdmin and current_user.weight == 0:
        raise Exception(trans("i18n_permission.no_permission", url="", msg=""))
    try:
        return build_identity_workspace_service(session).find_available_user(
            workspace_id=current_user.oid,
            keyword=keyword,
        )
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)


@router.get(
    "/uws/pager/{pageNum}/{pageSize}",
    response_model=PaginatedResponse[WorkspaceUser],
    include_in_schema=False,
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def pager(
    session: SessionDep,
    current_user: CurrentUser,
    trans: Trans,
    pageNum: int,
    pageSize: int,
    keyword: str | None = Query(None, description="搜索关键字(可选)"),
    oid: int | None = Query(None, description="空间ID(仅admin用户生效)"),
) -> PaginatedResponse[WorkspaceUser]:
    if not current_user.isAdmin and current_user.weight == 0:
        raise Exception(trans("i18n_permission.no_permission", url="", msg=""))
    workspace_id = oid or current_user.oid if current_user.isAdmin else current_user.oid
    try:
        return await build_identity_workspace_service(session).list_workspace_members(
            workspace_id=workspace_id,
            page=pageNum,
            size=pageSize,
            keyword=keyword,
        )
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)


@router.post(
    "/uws",
    summary=f"{PLACEHOLDER_PREFIX}ws_user_bind_api",
    description=f"{PLACEHOLDER_PREFIX}ws_user_bind_api",
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.ADD,
        module=OperationModules.MEMBER,
        resource_id_expr="creator.uid_list",
    )
)
async def create(
    session: SessionDep,
    current_user: CurrentUser,
    trans: Trans,
    creator: UserWsDTO,
) -> None:
    if not current_user.isAdmin and current_user.weight == 0:
        raise Exception(trans("i18n_permission.no_permission", url="", msg=""))
    workspace_id = (
        creator.oid if current_user.isAdmin and creator.oid else current_user.oid
    )
    weight = (
        creator.weight if current_user.isAdmin and creator.weight is not None else 0
    )
    try:
        build_identity_workspace_service(session).bind_members(
            workspace_id=workspace_id,
            user_ids=creator.uid_list,
            weight=weight,
        )
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    for user_id in dict.fromkeys(creator.uid_list):
        await clear_user_cache(user_id)


@router.put(
    "/uws",
    summary=f"{PLACEHOLDER_PREFIX}ws_user_status_api",
    description=f"{PLACEHOLDER_PREFIX}ws_user_status_api",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.MEMBER,
        resource_id_expr="editor.uid",
    )
)
async def uws_edit(
    session: SessionDep,
    trans: Trans,
    editor: UserWsEditor,
) -> None:
    await edit(session, trans, editor)


@router.delete(
    "/uws",
    summary=f"{PLACEHOLDER_PREFIX}ws_user_unbind_api",
    description=f"{PLACEHOLDER_PREFIX}ws_user_unbind_api",
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.DELETE,
        module=OperationModules.MEMBER,
        resource_id_expr="dto.uid_list",
    )
)
async def delete(
    session: SessionDep,
    current_user: CurrentUser,
    trans: Trans,
    dto: UserWsBase,
) -> None:
    if not current_user.isAdmin and current_user.weight == 0:
        raise Exception(trans("i18n_permission.no_permission", url="", msg=""))
    workspace_id = dto.oid if current_user.isAdmin and dto.oid else current_user.oid
    try:
        affected_user_ids = build_identity_workspace_service(session).unbind_members(
            workspace_id=workspace_id,
            user_ids=dto.uid_list,
        )
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    for user_id in affected_user_ids:
        await clear_user_cache(user_id)


@router.get(
    "",
    response_model=list[WorkspaceRecord],
    summary=f"{PLACEHOLDER_PREFIX}ws_all_api",
    description=f"{PLACEHOLDER_PREFIX}ws_all_api",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def query(session: SessionDep, trans: Trans) -> list[WorkspaceRecord]:
    workspaces = build_identity_workspace_service(session).list_workspaces()
    localized = [
        workspace.model_copy(
            update={
                "name": trans(workspace.name)
                if workspace.name.startswith("i18n")
                else workspace.name
            }
        )
        for workspace in workspaces
    ]
    return sorted(localized, key=lambda workspace: workspace.name)


@router.post(
    "",
    summary=f"{PLACEHOLDER_PREFIX}ws_create_api",
    description=f"{PLACEHOLDER_PREFIX}ws_create_api",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.CREATE,
        module=OperationModules.WORKSPACE,
        result_id_expr="id",
    )
)
async def add(session: SessionDep, creator: WorkspaceBase) -> WorkspaceRecord:
    return build_identity_workspace_service(session).create_workspace(
        creator,
        get_timestamp(),
    )


@router.put(
    "",
    summary=f"{PLACEHOLDER_PREFIX}ws_update_api",
    description=f"{PLACEHOLDER_PREFIX}ws_update_api",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.WORKSPACE,
        resource_id_expr="editor.id",
    )
)
async def update(
    session: SessionDep,
    trans: Trans,
    editor: WorkspaceEditor,
) -> WorkspaceRecord:
    # 该接口已由系统管理员权限拦截，此处只执行领域操作。
    try:
        return build_identity_workspace_service(session).update_workspace(editor)
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)


@router.get(
    "/{id}",
    response_model=WorkspaceRecord,
    summary=f"{PLACEHOLDER_PREFIX}ws_query_api",
    description=f"{PLACEHOLDER_PREFIX}ws_query_api",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def get_one(
    session: SessionDep,
    trans: Trans,
    id: int = Path(description=f"{PLACEHOLDER_PREFIX}oid"),
) -> WorkspaceRecord:
    try:
        workspace = build_identity_workspace_service(session).get_workspace(id)
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    if workspace.name.startswith("i18n"):
        return workspace.model_copy(update={"name": trans(workspace.name)})
    return workspace


@router.delete(
    "/{id}",
    summary=f"{PLACEHOLDER_PREFIX}ws_del_api",
    description=f"{PLACEHOLDER_PREFIX}ws_del_api",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.DELETE,
        module=OperationModules.WORKSPACE,
        resource_id_expr="id",
    )
)
async def single_delete(
    session: SessionDep,
    trans: Trans,
    id: int = Path(description=f"{PLACEHOLDER_PREFIX}oid"),
) -> None:
    try:
        affected_user_ids = build_identity_workspace_service(session).delete_workspace(id)
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    for user_id in affected_user_ids:
        await clear_user_cache(user_id)
