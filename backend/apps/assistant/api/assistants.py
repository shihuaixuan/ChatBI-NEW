"""Assistant 现有兼容路径的接口入口。"""

import json
import os
from datetime import timedelta

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlbot_xpack.file_utils import SQLBotFileUtils

from apps.access_control.permission import SqlbotPermission, require_permissions
from apps.assistant.composition import build_assistant_service
from apps.assistant.cors import update_dynamic_cors
from apps.assistant.models.dto import (
    AssistantBase,
    AssistantDTO,
    AssistantPublicInfo,
    AssistantRecord,
    AssistantUiSchema,
    AssistantValidator,
)
from apps.assistant.public import get_assistant_info
from apps.assistant.services import AssistantService
from common.interfaces.i18n import PLACEHOLDER_PREFIX
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.cache_keys import CacheName, CacheNamespace
from common.core.config import settings
from common.core.deps import (
    CurrentAssistant,
    CurrentUser,
    SessionDep,
    Trans,
)
from common.core.security import create_access_token
from common.core.sqlbot_cache import clear_cache
from common.utils.utils import get_origin_from_referer

router = APIRouter(tags=["system_assistant"], prefix="/system/assistant")


def _request_origin(request: Request) -> str | None:
    origin = request.headers.get("origin") or get_origin_from_referer(request)
    return origin.rstrip("/") if origin else None


def _require_allowed_origin(
    request: Request,
    domain: str,
    trans: Trans,
) -> str:
    origin = _request_origin(request)
    if not origin or not AssistantService.origin_is_allowed(origin, domain):
        raise RuntimeError(trans("i18n_embedded.invalid_origin", origin=origin or ""))
    return origin


@router.get(
    "/info/{id}",
    response_model=AssistantPublicInfo,
    include_in_schema=False,
)
async def info(
    request: Request,
    response: Response,
    session: SessionDep,
    trans: Trans,
    id: int,
) -> AssistantPublicInfo:
    if not id:
        raise ValueError("miss assistant id")
    assistant = build_assistant_service(session).get_public(id)
    response.headers["Access-Control-Allow-Origin"] = _require_allowed_origin(
        request,
        assistant.domain,
        trans,
    )
    return assistant


@router.get(
    "/app/{appId}",
    response_model=AssistantPublicInfo,
    include_in_schema=False,
)
async def get_app(
    request: Request,
    response: Response,
    session: SessionDep,
    trans: Trans,
    appId: str,
) -> AssistantPublicInfo:
    if not appId:
        raise ValueError("miss assistant appId")
    assistant = build_assistant_service(session).get_public_by_app_id(appId)
    response.headers["Access-Control-Allow-Origin"] = _require_allowed_origin(
        request,
        assistant.domain,
        trans,
    )
    return assistant


@router.get(
    "/validator",
    response_model=AssistantValidator,
    include_in_schema=False,
)
async def validator(
    session: SessionDep,
    id: int,
    virtual: int | None = Query(None),
) -> AssistantValidator:
    if not id:
        raise ValueError("miss assistant id")
    assistant = await get_assistant_info(session=session, assistant_id=id)
    if assistant is None:
        return AssistantValidator()

    expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        {
            "id": virtual,
            "account": "sqlbot-inner-assistant",
            "oid": assistant.oid,
            "assistant_id": id,
        },
        expires_delta=expires,
    )
    return AssistantValidator(
        valid=True,
        id_match=True,
        domain_match=True,
        token=access_token,
    )


@router.get(
    "/picture/{file_id}",
    summary=f"{PLACEHOLDER_PREFIX}assistant_picture_api",
    description=f"{PLACEHOLDER_PREFIX}assistant_picture_api",
)
async def picture(file_id: str = Path(description="file_id")):
    file_path = SQLBotFileUtils.get_file_path(file_id=file_id)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    media_type = "image/svg+xml" if file_id.lower().endswith(".svg") else "image/jpeg"

    def iterfile():
        with open(file_path, mode="rb") as file:
            yield from file

    return StreamingResponse(iterfile(), media_type=media_type)


@router.patch(
    "/ui",
    summary=f"{PLACEHOLDER_PREFIX}assistant_ui_api",
    description=f"{PLACEHOLDER_PREFIX}assistant_ui_api",
)
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.APPLICATION,
        result_id_expr="id",
    )
)
async def ui(
    session: SessionDep,
    current_user: CurrentUser,
    data: str = Form(),
    files: list[UploadFile] = File(default=[]),
):
    schema = AssistantUiSchema.model_validate(json.loads(data))
    uploaded_asset_ids: dict[str, str] = {}
    uploaded_ids_for_cleanup: list[str] = []
    try:
        for file in files:
            original_name = file.filename
            file_name, flag_name = SQLBotFileUtils.split_filename_and_flag(
                original_name
            )
            if flag_name not in {"logo", "float_icon"}:
                raise ValueError(f"Unsupported file flag: {flag_name}")
            if flag_name in uploaded_asset_ids:
                raise ValueError(f"Duplicate file flag: {flag_name}")
            file.filename = file_name
            try:
                SQLBotFileUtils.check_file(
                    file=file,
                    file_types=[".jpg", ".png", ".svg"],
                    limit_file_size=10 * 1024 * 1024,
                )
            except ValueError as exc:
                if "文件大小超过限制" in str(exc):
                    raise ValueError("文件大小超过限制（最大 10 M）") from exc
                raise
            file_id = await SQLBotFileUtils.upload(file)
            uploaded_asset_ids[flag_name] = file_id
            uploaded_ids_for_cleanup.append(file_id)

        result = build_assistant_service(session).update_ui(
            schema,
            uploaded_asset_ids,
            current_user.oid,
        )
    except Exception:
        for file_id in uploaded_ids_for_cleanup:
            SQLBotFileUtils.delete_file(file_id)
        raise

    for obsolete_asset_id in result.obsolete_asset_ids:
        SQLBotFileUtils.delete_file(obsolete_asset_id)
    await clear_ui_cache(result.assistant.id)
    return result.assistant


@clear_cache(
    namespace=CacheNamespace.EMBEDDED_INFO,
    cacheName=CacheName.ASSISTANT_INFO,
    keyExpression="id",
)
async def clear_ui_cache(id: int) -> None:
    del id
    return None


@router.get("/ds", include_in_schema=False, response_model=list[dict])
async def ds(
    session: SessionDep,
    current_assistant: CurrentAssistant,
) -> list[dict[str, object]]:
    return [
        item.model_dump()
        for item in build_assistant_service(session).list_datasources(current_assistant)
    ]


@router.get(
    "",
    response_model=list[AssistantRecord],
    summary=f"{PLACEHOLDER_PREFIX}assistant_grid_api",
    description=f"{PLACEHOLDER_PREFIX}assistant_grid_api",
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def query(
    session: SessionDep,
    current_user: CurrentUser,
) -> list[AssistantRecord]:
    return build_assistant_service(session).list_for_workspace(current_user.oid)


@router.get(
    "/advanced_application",
    response_model=list[AssistantRecord],
    include_in_schema=False,
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def query_advanced_application(
    session: SessionDep,
    current_user: CurrentUser,
) -> list[AssistantRecord]:
    return build_assistant_service(session).list_advanced_for_workspace(
        current_user.oid
    )


@router.post(
    "",
    summary=f"{PLACEHOLDER_PREFIX}assistant_create_api",
    description=f"{PLACEHOLDER_PREFIX}assistant_create_api",
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.CREATE,
        module=OperationModules.APPLICATION,
        result_id_expr="id",
    )
)
async def add(
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
    creator: AssistantBase,
) -> AssistantRecord:
    assistant = build_assistant_service(session).create(creator, current_user.oid)
    update_dynamic_cors(request.app, build_assistant_service(session))
    return assistant


@router.put(
    "",
    summary=f"{PLACEHOLDER_PREFIX}assistant_update_api",
    description=f"{PLACEHOLDER_PREFIX}assistant_update_api",
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
@clear_cache(
    namespace=CacheNamespace.EMBEDDED_INFO,
    cacheName=CacheName.ASSISTANT_INFO,
    keyExpression="editor.id",
)
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.APPLICATION,
        resource_id_expr="editor.id",
    )
)
async def update(
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
    editor: AssistantDTO,
) -> None:
    build_assistant_service(session).update(editor, current_user.oid)
    update_dynamic_cors(request.app, build_assistant_service(session))


@router.get(
    "/{id}",
    response_model=AssistantRecord,
    summary=f"{PLACEHOLDER_PREFIX}assistant_query_api",
    description=f"{PLACEHOLDER_PREFIX}assistant_query_api",
)
async def get_one(
    session: SessionDep,
    current_user: CurrentUser,
    id: int = Path(description="ID"),
) -> AssistantRecord:
    return build_assistant_service(session).get_for_workspace(id, current_user.oid)


@router.delete(
    "/{id}",
    summary=f"{PLACEHOLDER_PREFIX}assistant_del_api",
    description=f"{PLACEHOLDER_PREFIX}assistant_del_api",
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
@clear_cache(
    namespace=CacheNamespace.EMBEDDED_INFO,
    cacheName=CacheName.ASSISTANT_INFO,
    keyExpression="id",
)
@system_log(
    LogConfig(
        operation_type=OperationType.DELETE,
        module=OperationModules.APPLICATION,
        resource_id_expr="id",
    )
)
async def delete(
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
    id: int = Path(description="ID"),
) -> None:
    build_assistant_service(session).delete(id, current_user.oid)
    update_dynamic_cors(request.app, build_assistant_service(session))
