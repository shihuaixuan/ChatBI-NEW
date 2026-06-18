from __future__ import annotations

from pydantic import BaseModel, Field

from apps.semantic.assets.index_sync import AssetSyncReport
from apps.semantic.assets.models import (
    AssetRetrievalDocumentRuntime,
    CandidateGroup,
    DatasetProfileRuntime,
    RuntimeSchema,
)
from apps.semantic.assets.quality_service import AssetQualityResult


class AssetDebugScope(BaseModel):
    oid: int
    datasource_id: int
    dataset_id: int | str
    table_ids: list[int] = Field(default_factory=list)


class DatasetProfileDebugResponse(BaseModel):
    scope: AssetDebugScope
    profile: DatasetProfileRuntime


class RuntimeSchemaDebugResponse(BaseModel):
    scope: AssetDebugScope
    runtime_schema: RuntimeSchema


class CandidateGroupDebugResponse(BaseModel):
    scope: AssetDebugScope
    candidate_groups: CandidateGroup


class AssetDocumentDebugResponse(BaseModel):
    scope: AssetDebugScope
    document: AssetRetrievalDocumentRuntime | None = None


class AssetQualityDebugResponse(BaseModel):
    scope: AssetDebugScope
    results: list[AssetQualityResult] = Field(default_factory=list)


class AssetSyncDebugResponse(BaseModel):
    scope: AssetDebugScope
    report: AssetSyncReport


class AssetCacheInvalidateDebugResponse(BaseModel):
    scope: AssetDebugScope
    invalidated_cache_keys: int = 0
