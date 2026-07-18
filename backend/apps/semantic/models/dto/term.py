from typing import Any

from pydantic import Field

from apps.semantic.models.dto.base import SemanticBaseDTO


class TermPayload(SemanticBaseDTO):
    domain_id: int
    name: str
    alias: list[str] = Field(default_factory=list)
    description: str | None = None
    related_metrics: list[int] = Field(default_factory=list)
    related_dimensions: list[int] = Field(default_factory=list)
    related_datasets: list[int] = Field(default_factory=list)


class TermSearchResult(SemanticBaseDTO):
    term_id: int
    dataset_id: int
    words: list[str] = Field(default_factory=list)
    description: str | None = None
    related_assets: list[dict[str, Any]] = Field(default_factory=list)
