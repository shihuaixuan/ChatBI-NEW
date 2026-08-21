from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from apps.semantic.models.dto.base import SemanticBaseDTO


class MetricFormulaComponent(SemanticBaseDTO):
    """结构化公式中的指标角色。"""

    metric_id: int = Field(gt=0)
    role: str = Field(min_length=1)
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class MetricFormulaDefinition(SemanticBaseDTO):
    """派生指标的唯一公式事实源。"""

    operation: Literal["RATIO", "SUM", "DIFFERENCE", "PRODUCT"]
    components: list[MetricFormulaComponent] = Field(min_length=2)
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @model_validator(mode="after")
    def validate_components(self):
        roles = [item.role for item in self.components]
        if len(roles) != len(set(roles)):
            raise ValueError("SEMANTIC_METRIC_FORMULA_ROLE_DUPLICATED")
        required_roles = {
            "RATIO": {"numerator", "denominator"},
            "DIFFERENCE": {"minuend", "subtrahend"},
        }.get(self.operation)
        if required_roles is not None and set(roles) != required_roles:
            raise ValueError("SEMANTIC_METRIC_FORMULA_ROLE_INVALID")
        return self


class MetricPayload(SemanticBaseDTO):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

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
    formula_definition: MetricFormulaDefinition | None = None
    comparison_grains: list[Literal["day", "week", "month", "quarter", "year"]] = Field(
        default_factory=list
    )
    time_alignment_policy: Literal["SAME_TIME", "AS_OF", "NONE"] = "NONE"


class MetricBatchCreateFromMeasuresPayload(SemanticBaseDTO):
    model_id: int
    measure_ids: list[int] = Field(default_factory=list)
    measure_biz_names: list[str] = Field(default_factory=list)
