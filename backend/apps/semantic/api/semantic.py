import re

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from apps.datasource.models.datasource import CoreDatasource, CoreTable
from apps.semantic.crud.dimension import (
    approve_dimension,
    create_dimension,
    disable_dimension,
    get_dimension,
    page_dimensions,
    submit_dimension_embedding_rebuild,
    update_dimension,
)
from apps.semantic.crud.metric import (
    approve_metric,
    create_metric,
    disable_metric,
    get_metric,
    page_metrics,
    submit_metric_embedding_rebuild,
    update_metric,
)
from apps.semantic.crud.usage import list_chat_record_assets
from apps.semantic.models.semantic_model import AssetType
from apps.semantic.models.semantic_schema import (
    ChatRecordSemanticAssetInfo,
    ChatRecordSemanticAssetResponse,
    DimensionCreate,
    DimensionInfo,
    DimensionUpdate,
    DisableAssetRequest,
    InitializeSemanticRequest,
    InitializeSemanticResponse,
    MetricCreate,
    MetricInfo,
    MetricUpdate,
    RebuildEmbeddingResponse,
    ValidateExpressionRequest,
    ValidateExpressionResponse,
)
from apps.semantic.services.candidate_init import initialize_candidates
from apps.semantic.services.expression_validator import (
    validate_dimension_expr,
    validate_metric_expr,
)
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")

ERROR_STATUS = {
    "SEMANTIC_DATASOURCE_NOT_FOUND": 404,
    "SEMANTIC_ASSET_NOT_FOUND": 404,
    "SEMANTIC_ASSET_DUPLICATED": 400,
    "SEMANTIC_STATUS_INVALID": 400,
    "SEMANTIC_EXPR_INVALID": 400,
    "SEMANTIC_SOURCE_FIELD_MISSING": 400,
    "SEMANTIC_EMBEDDING_FAILED": 500,
    "SEMANTIC_PERMISSION_DENIED": 403,
}
SENSITIVE_ASSIGNMENT = re.compile(r"\b(password|token|configuration)\s*=\s*\S+", re.IGNORECASE)
CONNECTION_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)


def _sanitize_error_detail(message: str) -> str:
    message = CONNECTION_URL.sub("[REDACTED_DSN]", message)
    return SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", message)


def _semantic_error(exc: ValueError) -> HTTPException:
    message = _sanitize_error_detail(str(exc))
    code = message.split(":", 1)[0]
    return HTTPException(status_code=ERROR_STATUS.get(code, 400), detail=message)


def _ensure_datasource_access(session: SessionDep, oid: int, datasource_id: int) -> None:
    exists = session.execute(
        select(CoreDatasource.id).where(CoreDatasource.id == datasource_id, CoreDatasource.oid == oid)
    ).first()
    if exists is None:
        raise HTTPException(status_code=403, detail="SEMANTIC_PERMISSION_DENIED: datasource not found in current oid")


def _ensure_table_in_datasource(session: SessionDep, datasource_id: int, table_id: int) -> None:
    exists = session.execute(
        select(CoreTable.id).where(CoreTable.id == table_id, CoreTable.ds_id == datasource_id, CoreTable.checked.is_(True))
    ).first()
    if exists is None:
        raise HTTPException(status_code=400, detail="SEMANTIC_SOURCE_FIELD_MISSING: table not found")


@router.get("/metrics/page/{page}/{size}")
async def metric_page(
    session: SessionDep,
    current_user: CurrentUser,
    page: int,
    size: int,
    datasource_id: int,
    keyword: str | None = None,
    status: str | None = None,
    table_id: int | None = None,
    owner_id: int | None = None,
):
    _ensure_datasource_access(session, current_user.oid, datasource_id)
    current_page, page_size, total_count, metrics = page_metrics(
        session, current_user.oid, datasource_id, page, size, keyword, status, table_id, owner_id
    )
    return {
        "current_page": current_page,
        "page_size": page_size,
        "total_count": total_count,
        "data": [MetricInfo.model_validate(metric) for metric in metrics],
    }


@router.post("/metrics", response_model=MetricInfo)
async def metric_create(session: SessionDep, current_user: CurrentUser, payload: MetricCreate):
    _ensure_datasource_access(session, current_user.oid, payload.datasource_id)
    try:
        metric = create_metric(session, current_user.oid, payload, current_user.id)
        session.commit()
        session.refresh(metric)
        return metric
    except ValueError as exc:
        session.rollback()
        raise _semantic_error(exc) from exc


@router.put("/metrics/{id}", response_model=MetricInfo)
async def metric_update(session: SessionDep, current_user: CurrentUser, id: int, payload: MetricUpdate):
    try:
        metric = get_metric(session, current_user.oid, id)
        _ensure_datasource_access(session, current_user.oid, metric.datasource_id)
        updated = update_metric(session, current_user.oid, id, payload, current_user.id)
        session.commit()
        session.refresh(updated)
        return updated
    except ValueError as exc:
        session.rollback()
        raise _semantic_error(exc) from exc


@router.post("/metrics/{id}/approve")
async def metric_approve(session: SessionDep, current_user: CurrentUser, id: int):
    try:
        metric = get_metric(session, current_user.oid, id)
        _ensure_datasource_access(session, current_user.oid, metric.datasource_id)
        approved = approve_metric(session, current_user.oid, id, current_user.id)
        session.commit()
        session.refresh(approved)
        return {"id": approved.id, "status": approved.status, "validated": True}
    except ValueError as exc:
        session.rollback()
        raise _semantic_error(exc) from exc


@router.post("/metrics/{id}/disable", response_model=MetricInfo)
async def metric_disable(session: SessionDep, current_user: CurrentUser, id: int, payload: DisableAssetRequest):
    try:
        metric = get_metric(session, current_user.oid, id)
        _ensure_datasource_access(session, current_user.oid, metric.datasource_id)
        disabled = disable_metric(session, current_user.oid, id, payload.reason, current_user.id)
        session.commit()
        session.refresh(disabled)
        return disabled
    except ValueError as exc:
        session.rollback()
        raise _semantic_error(exc) from exc


@router.post("/metrics/{id}/embedding", response_model=RebuildEmbeddingResponse)
async def metric_embedding(session: SessionDep, current_user: CurrentUser, id: int):
    try:
        metric = get_metric(session, current_user.oid, id)
        _ensure_datasource_access(session, current_user.oid, metric.datasource_id)
        submit_metric_embedding_rebuild(metric.id)
        return RebuildEmbeddingResponse(id=metric.id)
    except ValueError as exc:
        raise _semantic_error(exc) from exc


@router.get("/dimensions/page/{page}/{size}")
async def dimension_page(
    session: SessionDep,
    current_user: CurrentUser,
    page: int,
    size: int,
    datasource_id: int,
    keyword: str | None = None,
    status: str | None = None,
    table_id: int | None = None,
    owner_id: int | None = None,
):
    _ensure_datasource_access(session, current_user.oid, datasource_id)
    current_page, page_size, total_count, dimensions = page_dimensions(
        session, current_user.oid, datasource_id, page, size, keyword, status, table_id, owner_id
    )
    return {
        "current_page": current_page,
        "page_size": page_size,
        "total_count": total_count,
        "data": [DimensionInfo.model_validate(dimension) for dimension in dimensions],
    }


@router.post("/dimensions", response_model=DimensionInfo)
async def dimension_create(session: SessionDep, current_user: CurrentUser, payload: DimensionCreate):
    _ensure_datasource_access(session, current_user.oid, payload.datasource_id)
    try:
        dimension = create_dimension(session, current_user.oid, payload, current_user.id)
        session.commit()
        session.refresh(dimension)
        return dimension
    except ValueError as exc:
        session.rollback()
        raise _semantic_error(exc) from exc


@router.put("/dimensions/{id}", response_model=DimensionInfo)
async def dimension_update(session: SessionDep, current_user: CurrentUser, id: int, payload: DimensionUpdate):
    try:
        dimension = get_dimension(session, current_user.oid, id)
        _ensure_datasource_access(session, current_user.oid, dimension.datasource_id)
        updated = update_dimension(session, current_user.oid, id, payload, current_user.id)
        session.commit()
        session.refresh(updated)
        return updated
    except ValueError as exc:
        session.rollback()
        raise _semantic_error(exc) from exc


@router.post("/dimensions/{id}/approve")
async def dimension_approve(session: SessionDep, current_user: CurrentUser, id: int):
    try:
        dimension = get_dimension(session, current_user.oid, id)
        _ensure_datasource_access(session, current_user.oid, dimension.datasource_id)
        approved = approve_dimension(session, current_user.oid, id, current_user.id)
        session.commit()
        session.refresh(approved)
        return {"id": approved.id, "status": approved.status, "validated": True}
    except ValueError as exc:
        session.rollback()
        raise _semantic_error(exc) from exc


@router.post("/dimensions/{id}/disable", response_model=DimensionInfo)
async def dimension_disable(session: SessionDep, current_user: CurrentUser, id: int, payload: DisableAssetRequest):
    try:
        dimension = get_dimension(session, current_user.oid, id)
        _ensure_datasource_access(session, current_user.oid, dimension.datasource_id)
        disabled = disable_dimension(session, current_user.oid, id, payload.reason, current_user.id)
        session.commit()
        session.refresh(disabled)
        return disabled
    except ValueError as exc:
        session.rollback()
        raise _semantic_error(exc) from exc


@router.post("/dimensions/{id}/embedding", response_model=RebuildEmbeddingResponse)
async def dimension_embedding(session: SessionDep, current_user: CurrentUser, id: int):
    try:
        dimension = get_dimension(session, current_user.oid, id)
        _ensure_datasource_access(session, current_user.oid, dimension.datasource_id)
        submit_dimension_embedding_rebuild(dimension.id)
        return RebuildEmbeddingResponse(id=dimension.id)
    except ValueError as exc:
        raise _semantic_error(exc) from exc


@router.post("/datasources/{datasource_id}/initialize", response_model=InitializeSemanticResponse)
async def datasource_initialize(
    session: SessionDep,
    current_user: CurrentUser,
    datasource_id: int,
    payload: InitializeSemanticRequest,
):
    _ensure_datasource_access(session, current_user.oid, datasource_id)
    response = initialize_candidates(
        session,
        oid=current_user.oid,
        datasource_id=datasource_id,
        table_ids=payload.table_ids,
        overwrite_candidate=payload.overwrite_candidate,
        user_id=current_user.id,
        dry_run=payload.dry_run,
    )
    if not payload.dry_run:
        session.commit()
    return response


@router.post("/datasources/{datasource_id}/validate", response_model=ValidateExpressionResponse)
async def datasource_validate(
    session: SessionDep,
    current_user: CurrentUser,
    datasource_id: int,
    payload: ValidateExpressionRequest,
):
    _ensure_datasource_access(session, current_user.oid, datasource_id)
    _ensure_table_in_datasource(session, datasource_id, payload.table_id)
    if payload.asset_type == AssetType.DIMENSION.value:
        return validate_dimension_expr(session, payload)
    return validate_metric_expr(session, payload)


@router.get("/chat-records/{record_id}/assets", response_model=ChatRecordSemanticAssetResponse)
async def chat_record_assets(session: SessionDep, current_user: CurrentUser, record_id: int):
    try:
        assets = list_chat_record_assets(session, current_user.oid, record_id)
        return ChatRecordSemanticAssetResponse(
            record_id=record_id,
            assets=[ChatRecordSemanticAssetInfo.model_validate(asset) for asset in assets],
        )
    except ValueError as exc:
        raise _semantic_error(exc) from exc
