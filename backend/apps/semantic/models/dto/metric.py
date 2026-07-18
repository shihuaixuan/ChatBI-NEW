from typing import Any

from pydantic import Field

from apps.semantic.models.dto.base import SemanticBaseDTO


class MetricPayload(SemanticBaseDTO):
    model_id: int
    name: str
    biz_name: str
    description: str | None = None
    alias: list[str] = Field(default_factory=list)
    default_agg: str | None = None
    type: str = "ATOMIC"
    define_type: str = "MEASURE"
    type_params: dict[str, Any] = Field(default_factory=dict)
    relate_dimensions: list[dict[str, Any]] = Field(default_factory=list)


class MetricBatchCreateFromMeasuresPayload(SemanticBaseDTO):
    model_id: int
    measure_ids: list[int] = Field(default_factory=list)
    measure_biz_names: list[str] = Field(default_factory=list)
