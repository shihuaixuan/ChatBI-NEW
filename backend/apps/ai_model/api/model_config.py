"""AI 模型配置 API。"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import StreamingResponse

from apps.access_control.permission import SqlbotPermission, require_permissions
from apps.ai_model.composition import build_ai_model_management_service
from apps.ai_model.errors import (
    AIModelConfigInvalidError,
    AIModelDefaultCannotDeleteError,
    AIModelDefaultChangeRequiresEndpointError,
    AIModelNotFoundError,
)
from apps.ai_model.model_factory import LLMFactory
from apps.ai_model.models.dto import (
    AiModelCreator,
    AiModelEditor,
    AiModelGridItem,
    AIModelRecord,
)
from apps.ai_model.services import AIModelManagementService
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.deps import SessionDep, Trans
from common.interfaces.i18n import PLACEHOLDER_PREFIX
from common.utils.utils import SQLBotLogUtil

router = APIRouter(tags=["system_model"], prefix="/system/aimodel")


@router.post("/status", include_in_schema=False)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def check_llm(info: AiModelCreator, trans: Trans) -> StreamingResponse:
    async def generate() -> AsyncIterator[str]:
        try:
            config = AIModelManagementService.build_validation_config(info)
            llm_instance = LLMFactory.create_llm(config)
            async for chunk in llm_instance.llm.astream("1+1=?"):
                content = chunk if isinstance(chunk, str) else getattr(chunk, "content", None)
                if content:
                    SQLBotLogUtil.info(content)
                    yield json.dumps({"content": content}, ensure_ascii=False) + "\n"
        except Exception as exc:
            # 模型供应商错误需要通过流式响应返回给当前检查页面。
            SQLBotLogUtil.error(f"Error checking LLM: {exc}")
            error_msg = trans("i18n_llm.validate_error", msg=str(exc))
            yield json.dumps({"error": error_msg}, ensure_ascii=False) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.get("/default", include_in_schema=False)
async def check_default(session: SessionDep, trans: Trans) -> None:
    if not build_ai_model_management_service(session).has_default():
        raise ValueError(trans("i18n_llm.miss_default"))


@router.put(
    "/default/{id}",
    summary=f"{PLACEHOLDER_PREFIX}system_model_default",
    description=f"{PLACEHOLDER_PREFIX}system_model_default",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.AI_MODEL,
        resource_id_expr="id",
    )
)
async def set_default(
    session: SessionDep,
    id: int = Path(description="ID"),
) -> AIModelRecord:
    try:
        return build_ai_model_management_service(session).set_default(id)
    except AIModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "",
    response_model=list[AiModelGridItem],
    summary=f"{PLACEHOLDER_PREFIX}system_model_grid",
    description=f"{PLACEHOLDER_PREFIX}system_model_grid",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def query(
    session: SessionDep,
    keyword: str | None = Query(
        default=None,
        max_length=255,
        description=f"{PLACEHOLDER_PREFIX}keyword",
    ),
) -> list[AiModelGridItem]:
    return build_ai_model_management_service(session).list_models(keyword)


@router.get(
    "/{id}",
    response_model=AiModelEditor,
    summary=f"{PLACEHOLDER_PREFIX}system_model_query",
    description=f"{PLACEHOLDER_PREFIX}system_model_query",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def get_model_by_id(
    session: SessionDep,
    id: int = Path(description="ID"),
) -> AiModelEditor:
    try:
        return await build_ai_model_management_service(session).get_model(id)
    except AIModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AIModelConfigInvalidError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "",
    summary=f"{PLACEHOLDER_PREFIX}system_model_create",
    description=f"{PLACEHOLDER_PREFIX}system_model_create",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.CREATE,
        module=OperationModules.AI_MODEL,
        result_id_expr="id",
    )
)
async def add_model(
    session: SessionDep,
    creator: AiModelCreator,
) -> AIModelRecord:
    try:
        return await build_ai_model_management_service(session).create_model(creator)
    except AIModelConfigInvalidError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put(
    "",
    summary=f"{PLACEHOLDER_PREFIX}system_model_update",
    description=f"{PLACEHOLDER_PREFIX}system_model_update",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.AI_MODEL,
        resource_id_expr="editor.id",
    )
)
async def update_model(
    session: SessionDep,
    editor: AiModelEditor,
) -> AIModelRecord:
    try:
        return await build_ai_model_management_service(session).update_model(editor)
    except AIModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AIModelDefaultChangeRequiresEndpointError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AIModelConfigInvalidError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete(
    "/{id}",
    summary=f"{PLACEHOLDER_PREFIX}system_model_del",
    description=f"{PLACEHOLDER_PREFIX}system_model_del",
)
@require_permissions(permission=SqlbotPermission(role=["admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.DELETE,
        module=OperationModules.AI_MODEL,
        resource_id_expr="id",
    )
)
async def delete_model(
    session: SessionDep,
    trans: Trans,
    id: int = Path(description="ID"),
) -> None:
    try:
        build_ai_model_management_service(session).delete_model(id)
    except AIModelDefaultCannotDeleteError as exc:
        raise ValueError(
            trans("i18n_llm.delete_default_error", key=exc.model_name)
        ) from exc
    except AIModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
