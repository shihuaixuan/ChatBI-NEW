from datetime import datetime
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


class LegacyTerminologyDTO(SemanticBaseDTO):
    """旧 `/system/terminology` 接口使用的过渡 DTO。"""

    id: int | None = None
    domain_id: int | None = None
    create_time: datetime | None = None
    word: str | None = None
    description: str | None = None
    other_words: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    specific_ds: bool = False
    datasource_ids: list[int] = Field(default_factory=list)
    dataset_ids: list[int] = Field(default_factory=list)
    mapped_assets: list[dict[str, Any]] = Field(default_factory=list)
    datasource_names: list[str] = Field(default_factory=list)
    enabled: bool = True
