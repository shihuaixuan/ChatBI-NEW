from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.semantic.models.semantic_model import (
    AggregationType,
    AssetStatus,
    AssetType,
    DimensionType,
    MetricDefineType,
    SemanticType,
)

CREATE_ALLOWED_STATUSES = {AssetStatus.CANDIDATE.value, AssetStatus.DISABLED.value}


class SemanticBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MetricBase(SemanticBaseModel):
    datasource_id: int
    table_id: int | None = None
    field_id: int | None = None
    name: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=128)
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    define_type: str = MetricDefineType.MEASURE.value
    expr: str = Field(..., min_length=1)
    default_agg: str = AggregationType.SUM.value
    filter_sql: str | None = None
    data_type: str | None = None
    data_format: str | None = None
    default_time_dimension_id: int | None = None
    related_dimension_ids: list[int] = Field(default_factory=list)
    owner_id: int | None = None
    status: str = AssetStatus.CANDIDATE.value


class MetricCreate(MetricBase):
    @field_validator("status")
    @classmethod
    def validate_create_status(cls, status: str) -> str:
        if status not in CREATE_ALLOWED_STATUSES:
            raise ValueError("status only allows CANDIDATE or DISABLED when creating metric")
        return status


class MetricUpdate(SemanticBaseModel):
    table_id: int | None = None
    field_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    aliases: list[str] | None = None
    description: str | None = None
    define_type: str | None = None
    expr: str | None = Field(default=None, min_length=1)
    default_agg: str | None = None
    filter_sql: str | None = None
    data_type: str | None = None
    data_format: str | None = None
    default_time_dimension_id: int | None = None
    related_dimension_ids: list[int] | None = None
    owner_id: int | None = None


class MetricInfo(MetricBase):
    id: int
    oid: int
    origin: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: int | None = None
    updated_by: int | None = None


class MetricPageQuery(SemanticBaseModel):
    datasource_id: int
    keyword: str | None = None
    status: str | None = None
    table_id: int | None = None
    owner_id: int | None = None


class DimensionBase(SemanticBaseModel):
    datasource_id: int
    table_id: int | None = None
    field_id: int | None = None
    name: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=128)
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    expr: str = Field(..., min_length=1)
    dimension_type: str = DimensionType.CATEGORY.value
    semantic_type: str = SemanticType.UNKNOWN.value
    data_type: str | None = None
    time_granularities: list[str] = Field(default_factory=list)
    default_values: list[str] = Field(default_factory=list)
    owner_id: int | None = None
    status: str = AssetStatus.CANDIDATE.value


class DimensionCreate(DimensionBase):
    @field_validator("status")
    @classmethod
    def validate_create_status(cls, status: str) -> str:
        if status not in CREATE_ALLOWED_STATUSES:
            raise ValueError("status only allows CANDIDATE or DISABLED when creating dimension")
        return status


class DimensionUpdate(SemanticBaseModel):
    table_id: int | None = None
    field_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    aliases: list[str] | None = None
    description: str | None = None
    expr: str | None = Field(default=None, min_length=1)
    dimension_type: str | None = None
    semantic_type: str | None = None
    data_type: str | None = None
    time_granularities: list[str] | None = None
    default_values: list[str] | None = None
    owner_id: int | None = None


class DimensionInfo(DimensionBase):
    id: int
    oid: int
    origin: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: int | None = None
    updated_by: int | None = None


class DimensionPageQuery(SemanticBaseModel):
    datasource_id: int
    keyword: str | None = None
    status: str | None = None
    table_id: int | None = None
    owner_id: int | None = None


class InitializeSemanticRequest(SemanticBaseModel):
    overwrite_candidate: bool = False
    table_ids: list[int] = Field(default_factory=list)
    dry_run: bool = False


class InitializeSemanticResponse(SemanticBaseModel):
    datasource_id: int
    created_metrics: int = 0
    updated_metrics: int = 0
    created_dimensions: int = 0
    updated_dimensions: int = 0
    skipped: int = 0


class ValidateExpressionRequest(SemanticBaseModel):
    asset_type: str = AssetType.METRIC.value
    table_id: int
    expr: str = Field(..., min_length=1)
    default_agg: str = AggregationType.SUM.value
    filter_sql: str | None = None


class ValidateExpressionResponse(SemanticBaseModel):
    valid: bool
    check_sql: str = ""
    warnings: list[str] = Field(default_factory=list)
    error: str = ""


class DisableAssetRequest(SemanticBaseModel):
    reason: str = ""


class RebuildEmbeddingResponse(SemanticBaseModel):
    id: int
    embedding_status: str = "REBUILD_SUBMITTED"


class SemanticAssetMatch(SemanticBaseModel):
    asset_type: str
    asset_id: int
    name: str
    display_name: str
    score: float
    match_word: str | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)


class SemanticRetrieveRequest(SemanticBaseModel):
    oid: int
    datasource_id: int
    question: str = Field(..., min_length=1)
    metric_top_k: int = 5
    dimension_top_k: int = 8
    approved_only: bool = True


class SemanticRetrieveResponse(SemanticBaseModel):
    prompt_text: str = ""
    metrics: list[SemanticAssetMatch] = Field(default_factory=list)
    dimensions: list[SemanticAssetMatch] = Field(default_factory=list)
    degraded: bool = False
    reason: str = ""


class ChatRecordSemanticAssetInfo(SemanticBaseModel):
    asset_type: str
    asset_id: int
    role: str
    match_word: str | None = None
    score: float | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)


class ChatRecordSemanticAssetResponse(SemanticBaseModel):
    record_id: int
    assets: list[ChatRecordSemanticAssetInfo] = Field(default_factory=list)
