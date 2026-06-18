from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from apps.datasource.models.datasource import CoreDatasource
from apps.semantic.assets.cache import runtime_asset_cache
from apps.semantic.assets.dataset_profile import DatasetScope
from apps.semantic.assets.document_builder import AssetDocumentBuilder
from apps.semantic.assets.enums import AssetType
from apps.semantic.assets.index_sync import AssetIndexSyncService
from apps.semantic.assets.quality_service import AssetQualityService
from apps.semantic.assets.runtime_service import RuntimeAssetService
from apps.semantic.assets.schemas import (
    AssetCacheInvalidateDebugResponse,
    AssetDebugScope,
    AssetDocumentDebugResponse,
    AssetQualityDebugResponse,
    AssetSyncDebugResponse,
    CandidateGroupDebugResponse,
    DatasetProfileDebugResponse,
    RuntimeSchemaDebugResponse,
)
from common.core.config import settings
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic Asset Debug"], prefix="/semantic/assets/debug")


@router.get("/profile", response_model=DatasetProfileDebugResponse)
async def debug_dataset_profile(
    session: SessionDep,
    current_user: CurrentUser,
    datasource_id: int,
    table_ids: list[int] | None = Query(default=None),
):
    scope = _build_scope(session, current_user.oid, datasource_id, table_ids)
    runtime_schema = RuntimeAssetService(session=session, cache=runtime_asset_cache).load_runtime_schema(scope)
    return DatasetProfileDebugResponse(scope=_debug_scope(scope), profile=runtime_schema.dataset_profile)


@router.get("/runtime-schema", response_model=RuntimeSchemaDebugResponse)
async def debug_runtime_schema(
    session: SessionDep,
    current_user: CurrentUser,
    datasource_id: int,
    table_ids: list[int] | None = Query(default=None),
):
    scope = _build_scope(session, current_user.oid, datasource_id, table_ids)
    runtime_schema = RuntimeAssetService(session=session, cache=runtime_asset_cache).load_runtime_schema(scope)
    return RuntimeSchemaDebugResponse(scope=_debug_scope(scope), runtime_schema=runtime_schema)


@router.get("/candidates", response_model=CandidateGroupDebugResponse)
async def debug_candidate_groups(
    session: SessionDep,
    current_user: CurrentUser,
    datasource_id: int,
    table_ids: list[int] | None = Query(default=None),
):
    scope = _build_scope(session, current_user.oid, datasource_id, table_ids)
    runtime_schema = RuntimeAssetService(session=session, cache=runtime_asset_cache).load_runtime_schema(scope)
    return CandidateGroupDebugResponse(scope=_debug_scope(scope), candidate_groups=runtime_schema.candidate_groups)


@router.get("/documents/{asset_type}/{asset_id}", response_model=AssetDocumentDebugResponse)
async def debug_asset_document(
    session: SessionDep,
    current_user: CurrentUser,
    asset_type: AssetType,
    asset_id: str,
    datasource_id: int,
    table_ids: list[int] | None = Query(default=None),
):
    scope = _build_scope(session, current_user.oid, datasource_id, table_ids)
    runtime_schema = RuntimeAssetService(session=session, cache=runtime_asset_cache).load_runtime_schema(scope)
    document = next(
        (
            item
            for item in runtime_schema.documents
            if item.asset_type == asset_type and str(item.asset_id) == str(asset_id)
        ),
        None,
    )
    return AssetDocumentDebugResponse(scope=_debug_scope(scope), document=document)


@router.get("/quality", response_model=AssetQualityDebugResponse)
async def debug_asset_quality(
    session: SessionDep,
    current_user: CurrentUser,
    datasource_id: int,
    table_ids: list[int] | None = Query(default=None),
):
    scope = _build_scope(session, current_user.oid, datasource_id, table_ids)
    assets = AssetDocumentBuilder(session=session).load_quality_assets(scope)
    results = AssetQualityService().check_dataset_assets(assets)
    return AssetQualityDebugResponse(scope=_debug_scope(scope), results=results)


@router.post("/sync/datasource", response_model=AssetSyncDebugResponse)
async def debug_sync_datasource(
    session: SessionDep,
    current_user: CurrentUser,
    datasource_id: int,
    table_ids: list[int] | None = Query(default=None),
    dry_run: bool = False,
):
    scope = _build_scope(session, current_user.oid, datasource_id, table_ids)
    report = AssetIndexSyncService().sync_datasource(
        oid=current_user.oid,
        datasource_id=datasource_id,
        table_ids=scope.table_ids,
        dry_run=dry_run,
    )
    return AssetSyncDebugResponse(scope=_debug_scope(scope), report=report)


@router.post("/sync/dataset", response_model=AssetSyncDebugResponse)
async def debug_sync_dataset(
    session: SessionDep,
    current_user: CurrentUser,
    datasource_id: int,
    table_ids: list[int] | None = Query(default=None),
    dry_run: bool = False,
):
    scope = _build_scope(session, current_user.oid, datasource_id, table_ids)
    report = AssetIndexSyncService().sync_dataset(scope, dry_run=dry_run)
    return AssetSyncDebugResponse(scope=_debug_scope(scope), report=report)


@router.post("/cache/invalidate", response_model=AssetCacheInvalidateDebugResponse)
async def debug_invalidate_asset_cache(
    session: SessionDep,
    current_user: CurrentUser,
    datasource_id: int,
    table_ids: list[int] | None = Query(default=None),
):
    scope = _build_scope(session, current_user.oid, datasource_id, table_ids)
    invalidated = runtime_asset_cache.invalidate_dataset(scope.dataset_id)
    return AssetCacheInvalidateDebugResponse(scope=_debug_scope(scope), invalidated_cache_keys=invalidated)


def _build_scope(session: SessionDep, oid: int, datasource_id: int, table_ids: list[int] | None) -> DatasetScope:
    _ensure_debug_enabled()
    _ensure_datasource_access(session, oid, datasource_id)
    return DatasetScope.virtual(oid=oid, datasource_id=datasource_id, table_ids=table_ids or [])


def _ensure_debug_enabled() -> None:
    if not settings.SEMANTIC_ASSET_DEBUG_API_ENABLED:
        raise HTTPException(status_code=404, detail="SEMANTIC_ASSET_DEBUG_API_DISABLED")


def _ensure_datasource_access(session: SessionDep, oid: int, datasource_id: int) -> None:
    exists = session.execute(
        select(CoreDatasource.id).where(CoreDatasource.id == datasource_id, CoreDatasource.oid == oid)
    ).first()
    if exists is None:
        raise HTTPException(status_code=403, detail="SEMANTIC_PERMISSION_DENIED: datasource not found in current oid")


def _debug_scope(scope: DatasetScope) -> AssetDebugScope:
    return AssetDebugScope(
        oid=scope.oid,
        datasource_id=scope.datasource_id or 0,
        dataset_id=scope.dataset_id,
        table_ids=scope.table_ids,
    )
