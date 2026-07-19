import asyncio

from fastapi import APIRouter, HTTPException, Path

from apps.access_control.permission import SqlbotPermission, require_permissions
from apps.datasource.composition import (
    build_datasource_physical_relation_service,
)
from apps.datasource.models.dto import PhysicalRelationCell
from apps.datasource.services import (
    DatasourceNotFoundError,
    DatasourcePhysicalRelationError,
)
from apps.swagger.i18n import PLACEHOLDER_PREFIX
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.deps import SessionDep

router = APIRouter(tags=["Table Relation"], prefix="/table_relation")


@router.post(
    "/save/{ds_id}",
    response_model=None,
    summary=f"{PLACEHOLDER_PREFIX}tr_save",
)
@require_permissions(
    permission=SqlbotPermission(
        role=["ws_admin"],
        keyExpression="ds_id",
        type="ds",
    )
)
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE_TABLE_RELATION,
        module=OperationModules.DATASOURCE,
        resource_id_expr="ds_id",
    )
)
async def save_relation(
    session: SessionDep,
    relation: list[PhysicalRelationCell],
    ds_id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_id"),
):
    service = build_datasource_physical_relation_service(session)
    try:
        await asyncio.to_thread(service.save_relations, ds_id, relation)
    except DatasourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DatasourcePhysicalRelationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return True


@router.post(
    "/get/{ds_id}",
    response_model=list[PhysicalRelationCell],
    response_model_exclude_none=True,
    summary=f"{PLACEHOLDER_PREFIX}tr_get",
)
@require_permissions(
    permission=SqlbotPermission(
        role=["ws_admin"],
        keyExpression="ds_id",
        type="ds",
    )
)
async def get_relation(
    session: SessionDep,
    ds_id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_id"),
):
    service = build_datasource_physical_relation_service(session)
    try:
        return await asyncio.to_thread(service.list_relations, ds_id)
    except DatasourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
