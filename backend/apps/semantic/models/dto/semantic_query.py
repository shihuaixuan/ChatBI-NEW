"""语义查询规划和验证的不可变 DTO。"""

from typing import Any

from pydantic import ConfigDict, Field

from apps.semantic.models.dto.base import SemanticBaseDTO
from apps.semantic.models.dto.semantic_validation import (
    SemanticPlanStatus,
    SemanticValidationCheck,
)


class SemanticQueryPlanningInput(SemanticBaseDTO):
    """语义层规划服务的受控输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: int = Field(gt=0)
    schema_version: int = Field(ge=0)
    contract_version: int = Field(ge=0)
    metric_ids: tuple[int, ...] = ()
    logical_dimension_ids: tuple[int, ...] = ()
    dimension_usages: dict[int, tuple[str, ...]] = Field(default_factory=dict)
    filters: tuple[dict[str, Any], ...] = ()
    time_range: dict[str, Any] | None = None
    time_dimension_id: int | None = Field(default=None, gt=0)
    time_grain: str | None = None
    select_mode: str = "aggregate"
    query_shape: dict[str, Any] = Field(default_factory=dict)
    having: tuple[dict[str, Any], ...] = ()
    time_offset: dict[str, Any] | None = None
    subplans: tuple[dict[str, Any], ...] = ()
    order_by: tuple[dict[str, Any], ...] = ()
    limit: int | None = Field(default=None, gt=0, le=1000)
    ratio_specs: tuple[dict[str, Any], ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )


class SemanticMetricBinding(SemanticBaseDTO):
    """计划中固定的指标资产和执行契约。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_id: int = Field(gt=0)
    model_id: int = Field(gt=0)
    version: int = Field(ge=0)
    aggregation: str | None = None
    result_grain: tuple[str, ...] = ()
    additivity: str | None = None
    time_semantics: str | None = None
    comparison_grains: tuple[str, ...] = ()
    time_alignment_policy: str = "NONE"
    metric_refs: tuple[int, ...] = ()


class SemanticDimensionBinding(SemanticBaseDTO):
    """计划中固定的业务维度到物理实例绑定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_dimension_id: int = Field(gt=0)
    physical_dimension_id: int = Field(gt=0)
    model_id: int = Field(gt=0)
    usages: tuple[str, ...] = ()
    version: int = Field(ge=0)
    relation_path: tuple[int, ...] = ()
    aggregation_safety: str


class SemanticFilterBinding(SemanticBaseDTO):
    """计划中已经绑定物理维度的筛选条件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    physical_dimension_id: int = Field(gt=0)
    operator: str = "="
    value: Any
    value_source: str = "USER"


class SemanticTimeBinding(SemanticBaseDTO):
    """计划中的时间语义、时间维度和范围。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    semantics: str = "NONE"
    dimension_id: int | None = Field(default=None, gt=0)
    time_range: dict[str, Any] | None = None
    grain: str | None = None
    snapshot_aggregation: str | None = None
    default_timezone: str = "UTC"
    calendar_type: str = "NATURAL"
    week_start_day: int = Field(default=1, ge=1, le=7)
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    holiday_calendar_key: str | None = None


class SemanticModelPlan(SemanticBaseDTO):
    """计划中的基础模型、关系路径和预聚合要求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_model_id: int = Field(gt=0)
    model_ids: tuple[int, ...] = ()
    relation_path: tuple[int, ...] = ()
    pre_aggregation_required: bool = False
    pre_aggregation_grain: tuple[str, ...] = ()


class SemanticAggregationPlan(SemanticBaseDTO):
    """计划中固定的聚合、去重和结果粒度。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    aggregations: dict[int, str | None] = Field(default_factory=dict)
    distinct_keys: dict[int, tuple[str, ...]] = Field(default_factory=dict)
    result_grain: tuple[str, ...] = ()
    snapshot_strategies: dict[int, str | None] = Field(default_factory=dict)


class SemanticQueryPlan(SemanticBaseDTO):
    """已完成资产、关系、时间和验证绑定的不可变查询计划。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str = Field(min_length=1)
    dataset_id: int = Field(gt=0)
    schema_version: int = Field(ge=0)
    contract_version: int = Field(ge=0)
    metrics: tuple[SemanticMetricBinding, ...] = ()
    dimensions: tuple[SemanticDimensionBinding, ...] = ()
    filters: tuple[SemanticFilterBinding, ...] = ()
    time_binding: SemanticTimeBinding = Field(default_factory=SemanticTimeBinding)
    model_plan: SemanticModelPlan
    aggregation_plan: SemanticAggregationPlan
    validation_status: SemanticPlanStatus
    validation_reason_codes: tuple[str, ...] = ()
    query_shape: dict[str, Any] = Field(default_factory=dict)
    having: tuple[dict[str, Any], ...] = ()
    time_offset: dict[str, Any] | None = None
    subplans: tuple[dict[str, Any], ...] = ()
    order_by: tuple[dict[str, Any], ...] = ()
    limit: int | None = Field(default=None, gt=0, le=1000)
    # 仅规范查询结果列名，资产绑定仍由 dimensions 保持不变。
    output_aliases: dict[int, str] = Field(default_factory=dict)
    fingerprint: str = Field(min_length=1)


class SemanticPlanValidationReport(SemanticBaseDTO):
    """查询计划验证报告。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SemanticPlanStatus
    checks: tuple[SemanticValidationCheck, ...] = ()
    reason_codes: tuple[str, ...] = ()
    alternatives: tuple[dict[str, Any], ...] = ()
    evidence: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "SemanticAggregationPlan",
    "SemanticDimensionBinding",
    "SemanticFilterBinding",
    "SemanticMetricBinding",
    "SemanticModelPlan",
    "SemanticPlanValidationReport",
    "SemanticQueryPlan",
    "SemanticQueryPlanningInput",
    "SemanticTimeBinding",
]
