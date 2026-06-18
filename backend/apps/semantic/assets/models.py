from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from apps.semantic.assets.enums import AssetType


class AssetEvidence(BaseModel):
    channel: str
    query_source: str | None = None
    matched_field: str | None = None
    matched_text: str | None = None
    score: float = 0.0
    reason: str | None = None

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("score must be between 0.0 and 1.0")
        return value


class AssetRetrievalDocumentRuntime(BaseModel):
    doc_id: str
    asset_type: AssetType
    asset_id: int | str
    dataset_id: int | str
    title: str
    aliases: list[str] = Field(default_factory=list)
    business_text: str | None = None
    technical_text: str | None = None
    related_terms: list[str] = Field(default_factory=list)
    related_examples: list[str] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    search_text: str
    version: str = "dynamic"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DatasetProfileRuntime(BaseModel):
    dataset_id: int | str
    business_domain: str | None = None
    core_metric_ids: list[int] = Field(default_factory=list)
    metric_groups: dict[str, list[int]] = Field(default_factory=dict)
    default_dimensions: list[int] = Field(default_factory=list)
    default_time_dimension: int | None = None
    common_terms: list[str] = Field(default_factory=list)
    overview_examples: list[str] = Field(default_factory=list)


class CandidateAsset(BaseModel):
    asset_type: AssetType
    asset_id: int | str
    dataset_id: int | str | None = None
    title: str
    score: float = 0.0
    evidence: list[AssetEvidence] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("score must be between 0.0 and 1.0")
        return value


class CandidateGroup(BaseModel):
    metrics: list[CandidateAsset] = Field(default_factory=list)
    dimensions: list[CandidateAsset] = Field(default_factory=list)
    dimension_values: list[CandidateAsset] = Field(default_factory=list)
    terms: list[CandidateAsset] = Field(default_factory=list)
    examples: list[CandidateAsset] = Field(default_factory=list)
    fields: list[CandidateAsset] = Field(default_factory=list)


class RuntimeSchema(BaseModel):
    dataset_profile: DatasetProfileRuntime
    documents: list[AssetRetrievalDocumentRuntime] = Field(default_factory=list)
    candidate_groups: CandidateGroup = Field(default_factory=CandidateGroup)
    index_version: str = "dynamic"

