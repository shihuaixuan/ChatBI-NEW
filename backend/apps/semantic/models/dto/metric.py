from typing import Any, Literal

from pydantic import Field

from apps.semantic.models.dto.base import SemanticBaseDTO


class MetricPayload(SemanticBaseDTO):
    model_id: int
    name: str
    biz_name: str
    description: str | None = None
    alias: list[str] = Field(default_factory=list)
    default_agg: Literal[
        "SUM", "COUNT", "COUNT_DISTINCT", "AVG", "MIN", "MAX", "CUSTOM"
    ] | None = None
    type: str = "ATOMIC"
    define_type: str = "MEASURE"
    type_params: dict[str, Any] = Field(default_factory=dict)
    relate_dimensions: list[dict[str, Any]] = Field(default_factory=list)
    result_grain: list[str] = Field(default_factory=list)
    additivity: Literal["FULL", "SEMI", "NON_ADDITIVE"] | None = None
    distinct_keys: list[str] = Field(default_factory=list)
    time_semantics: Literal[
        "EVENT", "SNAPSHOT", "PERIODIC_SNAPSHOT", "NONE"
    ] | None = None
    default_time_dimension_id: int | None = None
    snapshot_aggregation: Literal[
        "ENDING", "BEGINNING", "AVG", "MAX", "MIN"
    ] | None = None
    contract_version: int | None = None


class MetricBatchCreateFromMeasuresPayload(SemanticBaseDTO):
    model_id: int
    measure_ids: list[int] = Field(default_factory=list)
    measure_biz_names: list[str] = Field(default_factory=list)
