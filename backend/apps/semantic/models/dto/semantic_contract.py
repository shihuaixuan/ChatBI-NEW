"""完整语义契约的输入 DTO。"""

from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from apps.semantic.models.dto.base import SemanticBaseDTO
from apps.semantic.models.dto.metric import MetricFormulaDefinition
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
    usages: list[Literal["GROUP_BY", "FILTER", "DETAIL", "CONTRIBUTION"]] = Field(
        default_factory=list
    )
    binding_strategy: Literal["SAME_MODEL", "RELATION_PATH"]
    relation_path: list[int] = Field(default_factory=list)
    target_model_id: int
    physical_dimension_id: int | None = None
    aggregation_safety: Literal["SAFE", "PRE_AGGREGATE_REQUIRED", "FORBIDDEN"]
    pre_aggregation_grain: list[str] = Field(default_factory=list)
    time_alignment_policy: Literal["SAME_TIME", "AS_OF", "NONE"] = "NONE"
    contribution_tolerance: float = Field(default=1e-6, ge=0)
    ext: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_strategy(self) -> Self:
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


class DimensionHierarchyLevelPayload(SemanticBaseDTO):
    """维度层级节点入参；节点只引用稳定的逻辑维度。"""

    logical_dimension_id: int
    level_order: int = Field(gt=0)
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class DimensionHierarchyPayload(SemanticBaseDTO):
    """固定级别维度层级的创建和更新入参。"""

    domain_id: int
    name: str
    biz_name: str
    description: str | None = None
    hierarchy_type: Literal["FIXED_LEVEL"] = "FIXED_LEVEL"
    levels: list[DimensionHierarchyLevelPayload] = Field(min_length=2)
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @model_validator(mode="after")
    def validate_levels(self) -> Self:
        orders = [item.level_order for item in self.levels]
        dimension_ids = [item.logical_dimension_id for item in self.levels]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("SEMANTIC_HIERARCHY_LEVEL_ORDER_INVALID")
        if len(dimension_ids) != len(set(dimension_ids)):
            raise ValueError("SEMANTIC_HIERARCHY_DIMENSION_DUPLICATED")
        return self


class DimensionHierarchyLevelDTO(SemanticBaseDTO):
    """发布到公开 Schema 的层级节点。"""

    id: int
    logical_dimension_id: int
    level_order: int
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class DimensionHierarchyDTO(SemanticBaseDTO):
    """语义治理 API 返回的维度层级。"""

    id: int
    oid: int
    domain_id: int
    name: str
    biz_name: str
    description: str | None = None
    hierarchy_type: str
    contract_status: str
    version: int
    status: int
    levels: list[DimensionHierarchyLevelDTO] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class DimensionHierarchyRuntimeLevelDTO(SemanticBaseDTO):
    """运行时层级节点及其当前数据集物理绑定。"""

    logical_dimension_id: int
    level_order: int
    physical_dimension_ids: tuple[int, ...] = ()
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class DimensionHierarchyRuntimeDTO(SemanticBaseDTO):
    """DatasetSchema 中冻结的维度层级契约。"""

    id: int
    domain_id: int
    hierarchy_type: Literal["FIXED_LEVEL"]
    contract_status: Literal["CERTIFIED"]
    version: int
    levels: tuple[DimensionHierarchyRuntimeLevelDTO, ...] = Field(min_length=2)
    dimension_refs_by_model: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class MetricRelationshipPayload(SemanticBaseDTO):
    """指标关系的创建和更新入参。"""

    domain_id: int
    target_metric_id: int
    driver_metric_id: int
    relationship_type: Literal[
        "FORMULA_COMPONENT",
        "CERTIFIED_DRIVER",
        "GOVERNED_ANALYSIS_RELATION",
    ]
    validation_method: Literal[
        "SAME_DIRECTION",
        "OPPOSITE_DIRECTION",
        "FORMULA_RECONCILIATION",
    ]
    expected_direction: Literal["POSITIVE", "NEGATIVE", "UNKNOWN"] = "UNKNOWN"
    supported_time_roles: list[str] = Field(default_factory=list)
    logical_dimension_ids: list[int] = Field(default_factory=list)
    relation_path: list[int] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @model_validator(mode="after")
    def validate_relationship(self) -> Self:
        if self.target_metric_id == self.driver_metric_id:
            raise ValueError("SEMANTIC_METRIC_RELATIONSHIP_SELF_REFERENCE")
        if len(self.logical_dimension_ids) != len(set(self.logical_dimension_ids)):
            raise ValueError("SEMANTIC_METRIC_RELATIONSHIP_DIMENSION_DUPLICATED")
        if len(self.supported_time_roles) != len(set(self.supported_time_roles)):
            raise ValueError("SEMANTIC_METRIC_RELATIONSHIP_TIME_ROLE_DUPLICATED")
        if (
            self.relationship_type == "FORMULA_COMPONENT"
            and self.validation_method != "FORMULA_RECONCILIATION"
        ):
            raise ValueError("SEMANTIC_FORMULA_RELATIONSHIP_VALIDATION_INVALID")
        _validate_direction_contract(
            self.validation_method,
            self.expected_direction,
        )
        return self


class MetricRelationshipDTO(SemanticBaseDTO):
    """语义治理 API 返回的指标关系。"""

    id: int
    oid: int
    domain_id: int
    target_metric_id: int
    driver_metric_id: int
    relationship_type: str
    validation_method: str
    expected_direction: str
    supported_time_roles: list[str] = Field(default_factory=list)
    logical_dimension_ids: list[int] = Field(default_factory=list)
    relation_path: list[int] = Field(default_factory=list)
    contract_status: str
    version: int
    status: int
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class MetricRelationshipRuntimeDTO(SemanticBaseDTO):
    """DatasetSchema 中冻结的指标关系契约。"""

    id: str
    target_metric_ref: str
    driver_metric_ref: str
    component_metric_refs: tuple[str, ...] = ()
    relationship_type: Literal[
        "formula_component",
        "certified_driver",
        "governed_analysis_relation",
    ]
    validation_method: Literal[
        "SAME_DIRECTION",
        "OPPOSITE_DIRECTION",
        "FORMULA_RECONCILIATION",
    ]
    expected_direction: Literal["POSITIVE", "NEGATIVE", "UNKNOWN"]
    dimension_refs: tuple[str, ...] = ()
    dimension_refs_by_model: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    relation_path: tuple[int, ...] = ()
    time_roles: tuple[str, ...] = Field(min_length=1)
    relationship_fingerprint: str = Field(min_length=1)
    status: Literal["CERTIFIED"] = "CERTIFIED"
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @model_validator(mode="after")
    def validate_runtime_relationship(self) -> "MetricRelationshipRuntimeDTO":
        if self.relationship_type == "formula_component":
            if not self.component_metric_refs:
                raise ValueError("SEMANTIC_FORMULA_COMPONENT_METRICS_REQUIRED")
            if self.driver_metric_ref not in self.component_metric_refs:
                raise ValueError("SEMANTIC_FORMULA_DRIVER_NOT_IN_COMPONENTS")
        elif self.component_metric_refs:
            raise ValueError("SEMANTIC_NON_FORMULA_COMPONENT_METRICS_FORBIDDEN")
        if len(self.component_metric_refs) != len(set(self.component_metric_refs)):
            raise ValueError("SEMANTIC_FORMULA_COMPONENT_METRICS_DUPLICATED")
        _validate_direction_contract(
            self.validation_method,
            self.expected_direction,
        )
        return self


def _validate_direction_contract(
    validation_method: str,
    expected_direction: str,
) -> None:
    """关系验证方法和预期方向只能表达一套一致语义。"""

    if validation_method == "FORMULA_RECONCILIATION":
        if expected_direction != "UNKNOWN":
            raise ValueError("SEMANTIC_FORMULA_DIRECTION_MUST_BE_UNKNOWN")
        return
    if validation_method == "SAME_DIRECTION" and expected_direction == "NEGATIVE":
        raise ValueError("SEMANTIC_METRIC_RELATIONSHIP_DIRECTION_CONFLICT")
    if validation_method == "OPPOSITE_DIRECTION" and expected_direction == "POSITIVE":
        raise ValueError("SEMANTIC_METRIC_RELATIONSHIP_DIRECTION_CONFLICT")


class AnalysisOperationRejection(SemanticBaseDTO):
    """单个分析动作的服务端拒绝原因。"""

    operation: str
    reason: str


class MetricAnalysisCapabilities(SemanticBaseDTO):
    """某个指标在当前数据集中的可执行分析能力。"""

    metric_id: int
    available_operations: list[str] = Field(default_factory=list)
    rejected_operations: list[AnalysisOperationRejection] = Field(default_factory=list)


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
    def validate_entity_reference(self) -> Self:
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
    def validate_logical_dimension_reference(self) -> Self:
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
    snapshot_aggregation: Literal["ENDING", "BEGINNING", "AVG", "MAX", "MIN"] | None = (
        None
    )
    formula_definition: MetricFormulaDefinition | None = None
    comparison_grains: list[Literal["day", "week", "month", "quarter", "year"]] = Field(
        default_factory=list
    )
    time_alignment_policy: Literal["SAME_TIME", "AS_OF", "NONE"] = "NONE"


class MetricCapabilityBuildInput(SemanticBaseDTO):
    """新模型指标使用业务维度的同模型能力。"""

    metric_biz_name: str = Field(min_length=1)
    logical_dimension_id: int | None = None
    logical_dimension_reference: str | None = None
    usages: list[Literal["GROUP_BY", "FILTER", "DETAIL", "CONTRIBUTION"]] = Field(
        default_factory=list
    )
    physical_dimension_biz_name: str = Field(min_length=1)
    aggregation_safety: Literal["SAFE", "PRE_AGGREGATE_REQUIRED", "FORBIDDEN"]
    pre_aggregation_grain: list[str] = Field(default_factory=list)
    time_alignment_policy: Literal["SAME_TIME", "AS_OF", "NONE"] = "NONE"
    contribution_tolerance: float = Field(default=1e-6, ge=0)

    @model_validator(mode="after")
    def validate_logical_dimension_reference(self) -> Self:
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
    business_entities: list[ReferencedBusinessEntityInput] = Field(default_factory=list)
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
    "DimensionHierarchyLevelPayload",
    "DimensionHierarchyPayload",
    "DimensionHierarchyLevelDTO",
    "DimensionHierarchyDTO",
    "DimensionHierarchyRuntimeLevelDTO",
    "DimensionHierarchyRuntimeDTO",
    "LogicalDimensionPayload",
    "MetricDimensionCapabilityPayload",
    "MetricRelationshipPayload",
    "MetricRelationshipDTO",
    "MetricRelationshipRuntimeDTO",
    "AnalysisOperationRejection",
    "MetricAnalysisCapabilities",
    "MetricCapabilityBuildInput",
    "MetricContractBuildInput",
    "PhysicalDimensionBindingInput",
    "ReferencedBusinessEntityInput",
    "ReferencedLogicalDimensionInput",
    "SemanticContractBuildInput",
    "SemanticContractBuildResult",
]
