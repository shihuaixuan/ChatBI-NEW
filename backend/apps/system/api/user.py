"""用户 Excel 导入导出兼容接口。"""

from fastapi import APIRouter, File, UploadFile

from apps.access_control.composition import build_identity_workspace_service
from apps.access_control.errors import AccessControlError
from apps.access_control.http_error_mapping import raise_identity_http_error
from apps.access_control.models.dto import UserCreator
from apps.access_control.permission import SqlbotPermission, require_permissions
from apps.system.crud.user_excel import batchUpload, download_error_file, downTemplate
from common.core.deps import SessionDep, Trans

router = APIRouter(tags=["system_user"], prefix="/user")


async def create(session: SessionDep, creator: UserCreator, trans: Trans):
    """XPack 保留的创建用户入口，业务规则由 Access Control 执行。"""
    try:
        return build_identity_workspace_service(session).create_user(creator)
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)


@router.get("/template", include_in_schema=False)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def template_excel(trans: Trans):
    return await downTemplate(trans)


@router.post("/batchImport", include_in_schema=False)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def upload_excel(
    session: SessionDep,
    trans: Trans,
    file: UploadFile = File(...),
):
    return await batchUpload(session, trans, file)


@router.get("/errorRecord/{file_id}", include_in_schema=False)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def download_error(file_id: str):
    return download_error_file(file_id)


__all__ = ["create", "router"]
