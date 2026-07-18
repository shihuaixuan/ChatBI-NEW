from typing import Any

from pydantic import Field

from apps.semantic.models.dto.base import SemanticBaseDTO


class DimensionPayload(SemanticBaseDTO):
    model_id: int
    name: str
    biz_name: str
    description: str | None = None
    type: str = "categorical"
    semantic_type: str | None = None
    alias: list[str] = Field(default_factory=list)
    default_values: list[str] = Field(default_factory=list)
    dim_value_maps: list[dict[str, Any]] = Field(default_factory=list)
    type_params: dict[str, Any] = Field(default_factory=dict)
    expr: str | None = None
    data_type: str | None = None
