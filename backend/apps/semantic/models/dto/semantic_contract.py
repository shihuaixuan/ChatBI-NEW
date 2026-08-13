"""完整语义契约的输入 DTO。"""

from typing import Any, Literal

from pydantic import Field, model_validator

from apps.semantic.models.dto.base import SemanticBaseDTO
from apps.semantic.models.dto.model import ModelCreateWithAssetsPayload


class BusinessEntityPayload(SemanticBaseDTO):
    """业务实体创建和更新入参。"""

    domain_id: int
    name: str
    biz_name: str
    description: str | None = None
    key_type: str
    value_domain_key: str | None = None


class LogicalDimensionPayload(SemanticBaseDTO):
    """业务维度创建和更新入参。"""

    domain_id: int
    entity_id: int | None = None
    name: str
    biz_name: str
    description: str | None = None
    semantic_type: str
    value_type: str
    value_domain_key: str | None = None


class MetricDimensionCapabilityPayload(SemanticBaseDTO):
    """指标维度能力创建和更新入参。"""

    metric_id: int
    logical_dimension_id: int
    usages: list[Literal["GROUP_BY", "FILTER", "DETAIL"]] = Field(
        default_factory=list
    )
    binding_strategy: Literal["SAME_MODEL", "RELATION_PATH"]
    relation_path: list[int] = Field(default_factory=list)
    target_model_id: int
    physical_dimension_id: int | None = None
    aggregation_safety: Literal["SAFE", "PRE_AGGREGATE_REQUIRED", "FORBIDDEN"]
    pre_aggregation_grain: list[str] = Field(default_factory=list)
    time_alignment_policy: Literal["SAME_TIME", "AS_OF", "NONE"] = "NONE"
    ext: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_strategy(self):
        if not self.usages:
            raise ValueError("SEMANTIC_CAPABILITY_USAGE_REQUIRED")
        if self.binding_strategy == "SAME_MODEL" and self.relation_path:
            raise ValueError("SEMANTIC_SAME_MODEL_RELATION_PATH_FORBIDDEN")
        if self.binding_strategy == "RELATION_PATH" and not self.relation_path:
            raise ValueError("SEMANTIC_RELATION_PATH_REQUIRED")
        if (
            self.aggregation_safety == "PRE_AGGREGATE_REQUIRED"
            and not self.pre_aggregation_grain
        ):
            raise ValueError("SEMANTIC_PRE_AGGREGATION_GRAIN_REQUIRED")
        return self


class ReferencedBusinessEntityInput(BusinessEntityPayload):
    """统一构建流程中新建业务实体及其本地引用。"""

    reference: str = Field(min_length=1)


class ReferencedLogicalDimensionInput(SemanticBaseDTO):
    """统一构建流程中新建业务维度及其实体引用。"""

    reference: str = Field(min_length=1)
    domain_id: int
    entity_id: int | None = None
    entity_reference: str | None = None
    name: str
    biz_name: str
    description: str | None = None
    semantic_type: str
    value_type: str
    value_domain_key: str | None = None

    @model_validator(mode="after")
    def validate_entity_reference(self):
        if self.entity_id is not None and self.entity_reference:
            raise ValueError("SEMANTIC_LOGICAL_DIMENSION_ENTITY_REFERENCE_CONFLICT")
        return self


class PhysicalDimensionBindingInput(SemanticBaseDTO):
    """新模型内物理维度到业务维度的绑定。"""

    dimension_biz_name: str = Field(min_length=1)
    logical_dimension_id: int | None = None
    logical_dimension_reference: str | None = None
    binding_role: Literal["KEY", "ATTRIBUTE", "TIME"]
    binding_priority: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_logical_dimension_reference(self):
        if (self.logical_dimension_id is None) == (
            self.logical_dimension_reference is None
        ):
            raise ValueError("SEMANTIC_LOGICAL_DIMENSION_REFERENCE_REQUIRED")
        return self


class MetricContractBuildInput(SemanticBaseDTO):
    """从模型度量创建指标时一并确认的执行契约。"""

    metric_biz_name: str = Field(min_length=1)
    name: str | None = None
    description: str | None = None
    default_agg: Literal[
        "SUM", "COUNT", "COUNT_DISTINCT", "AVG", "MIN", "MAX", "CUSTOM"
    ]
    result_grain: list[str] = Field(default_factory=list)
    additivity: Literal["FULL", "SEMI", "NON_ADDITIVE"]
    distinct_keys: list[str] = Field(default_factory=list)
    time_semantics: Literal["EVENT", "SNAPSHOT", "PERIODIC_SNAPSHOT", "NONE"]
    default_time_dimension_biz_name: str | None = None
    snapshot_aggregation: Literal["ENDING", "BEGINNING", "AVG", "MAX", "MIN"] | None = None


class MetricCapabilityBuildInput(SemanticBaseDTO):
    """新模型指标使用业务维度的同模型能力。"""

    metric_biz_name: str = Field(min_length=1)
    logical_dimension_id: int | None = None
    logical_dimension_reference: str | None = None
    usages: list[Literal["GROUP_BY", "FILTER", "DETAIL"]] = Field(
        default_factory=list
    )
    physical_dimension_biz_name: str = Field(min_length=1)
    aggregation_safety: Literal["SAFE", "PRE_AGGREGATE_REQUIRED", "FORBIDDEN"]
    pre_aggregation_grain: list[str] = Field(default_factory=list)
    time_alignment_policy: Literal["SAME_TIME", "AS_OF", "NONE"] = "NONE"

    @model_validator(mode="after")
    def validate_logical_dimension_reference(self):
        if (self.logical_dimension_id is None) == (
            self.logical_dimension_reference is None
        ):
            raise ValueError("SEMANTIC_LOGICAL_DIMENSION_REFERENCE_REQUIRED")
        if not self.usages:
            raise ValueError("SEMANTIC_CAPABILITY_USAGE_REQUIRED")
        if (
            self.aggregation_safety == "PRE_AGGREGATE_REQUIRED"
            and not self.pre_aggregation_grain
        ):
            raise ValueError("SEMANTIC_PRE_AGGREGATION_GRAIN_REQUIRED")
        return self


class SemanticContractBuildInput(SemanticBaseDTO):
    """从单个物理表构建并原子保存语义契约资产。"""

    model: ModelCreateWithAssetsPayload
    business_entities: list[ReferencedBusinessEntityInput] = Field(
        default_factory=list
    )
    logical_dimensions: list[ReferencedLogicalDimensionInput] = Field(
        default_factory=list
    )
    dimension_bindings: list[PhysicalDimensionBindingInput] = Field(
        default_factory=list
    )
    metrics: list[MetricContractBuildInput] = Field(default_factory=list)
    capabilities: list[MetricCapabilityBuildInput] = Field(default_factory=list)


class SemanticContractBuildResult(SemanticBaseDTO):
    """统一构建入库后返回的稳定资产标识。"""

    model_id: int
    dimension_ids: dict[str, int] = Field(default_factory=dict)
    metric_ids: dict[str, int] = Field(default_factory=dict)
    business_entity_ids: dict[str, int] = Field(default_factory=dict)
    logical_dimension_ids: dict[str, int] = Field(default_factory=dict)
    capability_ids: list[int] = Field(default_factory=list)
    contract_status: str = "DRAFT"

__all__ = [
    "BusinessEntityPayload",
    "LogicalDimensionPayload",
    "MetricDimensionCapabilityPayload",
    "MetricCapabilityBuildInput",
    "MetricContractBuildInput",
    "PhysicalDimensionBindingInput",
    "ReferencedBusinessEntityInput",
    "ReferencedLogicalDimensionInput",
    "SemanticContractBuildInput",
    "SemanticContractBuildResult",
]
