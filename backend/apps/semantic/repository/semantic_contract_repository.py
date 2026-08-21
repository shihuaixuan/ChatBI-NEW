"""完整语义契约的持久化端口。"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from apps.semantic.models.orm import (
    BusinessEntity,
    DimensionHierarchy,
    DimensionHierarchyLevel,
    LogicalDimension,
    MetricDimensionCapability,
    MetricRelationship,
    MetricRelationshipDimension,
)
from apps.semantic.models.orm.contract_version import SemanticContractVersion
from apps.semantic.repository.model_repository import SemanticModelAssetBundle
from apps.semantic.repository.schema_repository import DatasetSchemaAssets


@dataclass
class PendingDimensionBinding:
    """等待数据库主键生成后应用的物理维度绑定。"""

    dimension_biz_name: str
    logical_dimension_id: int | None
    logical_dimension_reference: str | None
    binding_role: str
    binding_priority: int


@dataclass
class PendingMetricCapability:
    """等待模型内资产主键生成后创建的指标维度能力。"""

    metric_biz_name: str
    logical_dimension_id: int | None
    logical_dimension_reference: str | None
    physical_dimension_biz_name: str
    usages: list[str]
    aggregation_safety: str
    pre_aggregation_grain: list[str]
    time_alignment_policy: str
    contribution_tolerance: float


@dataclass
class SemanticContractAssetBundle:
    """统一构建流程一次事务保存的全部资产。"""

    model_assets: SemanticModelAssetBundle
    business_entities: dict[str, BusinessEntity] = field(default_factory=dict)
    logical_dimensions: dict[str, LogicalDimension] = field(default_factory=dict)
    logical_dimension_entity_references: dict[str, str] = field(default_factory=dict)
    dimension_bindings: list[PendingDimensionBinding] = field(default_factory=list)
    metric_default_time_dimensions: dict[str, str] = field(default_factory=dict)
    capabilities: list[PendingMetricCapability] = field(default_factory=list)


@dataclass
class StoredSemanticContractAssets:
    """统一构建流程保存后的资产集合。"""

    model_assets: SemanticModelAssetBundle
    business_entities: dict[str, BusinessEntity]
    logical_dimensions: dict[str, LogicalDimension]
    capabilities: list[MetricDimensionCapability]


@dataclass(frozen=True, slots=True)
class CapabilityRelationReference:
    """能力契约关系路径校验所需的最小关系事实。"""

    relation_id: int
    left_model_id: int
    right_model_id: int


@dataclass(frozen=True, slots=True)
class CapabilityReferenceFacts:
    """能力契约引用校验所需的租户内事实。"""

    metric_model_id: int | None
    logical_dimension_domain_id: int | None
    target_model_domain_id: int | None
    physical_dimension_model_id: int | None
    physical_dimension_logical_id: int | None
    relations: tuple[CapabilityRelationReference, ...]


class SemanticContractRepository(Protocol):
    """阶段1契约对象的基础增删改查端口。"""

    def list_business_entities(self, oid: int) -> list[BusinessEntity]: ...

    def get_business_entity(
        self, oid: int, entity_id: int
    ) -> BusinessEntity | None: ...

    def create_business_entity(self, entity: BusinessEntity) -> BusinessEntity: ...

    def update_business_entity(self, entity: BusinessEntity) -> BusinessEntity: ...

    def delete_business_entity(self, entity: BusinessEntity) -> None: ...

    def list_logical_dimensions(
        self, oid: int, domain_id: int | None = None
    ) -> list[LogicalDimension]: ...

    def get_logical_dimension(
        self, oid: int, dimension_id: int
    ) -> LogicalDimension | None: ...

    def create_logical_dimension(
        self, dimension: LogicalDimension
    ) -> LogicalDimension: ...

    def update_logical_dimension(
        self, dimension: LogicalDimension
    ) -> LogicalDimension: ...

    def delete_logical_dimension(self, dimension: LogicalDimension) -> None: ...

    def list_metric_dimension_capabilities(
        self,
        oid: int,
        metric_id: int | None = None,
    ) -> list[MetricDimensionCapability]: ...

    def get_metric_dimension_capability(
        self,
        oid: int,
        capability_id: int,
    ) -> MetricDimensionCapability | None: ...

    def create_metric_dimension_capability(
        self,
        capability: MetricDimensionCapability,
    ) -> MetricDimensionCapability: ...

    def update_metric_dimension_capability(
        self,
        capability: MetricDimensionCapability,
    ) -> MetricDimensionCapability: ...

    def delete_metric_dimension_capability(
        self,
        capability: MetricDimensionCapability,
    ) -> None: ...

    def metric_dimension_capability_is_referenced(
        self,
        oid: int,
        capability_id: int,
    ) -> bool: ...

    def invalidate_published_contracts_for_capability(
        self,
        oid: int,
        capability: MetricDimensionCapability,
    ) -> None: ...

    def create_contract_assets(
        self,
        bundle: SemanticContractAssetBundle,
    ) -> StoredSemanticContractAssets: ...

    def capability_reference_facts(
        self,
        oid: int,
        metric_id: int,
        logical_dimension_id: int,
        target_model_id: int,
        physical_dimension_id: int | None,
        relation_path: list[int],
    ) -> CapabilityReferenceFacts: ...

    def business_entity_is_referenced(self, oid: int, entity_id: int) -> bool: ...

    def logical_dimension_is_referenced(
        self, oid: int, logical_dimension_id: int
    ) -> bool: ...

    def publish_contract(
        self,
        assets: DatasetSchemaAssets,
        *,
        contract_version: int | None = None,
        schema_fingerprint: str = "",
        asset_snapshot: dict[str, object] | None = None,
        published_by: int | None = None,
        build_snapshot: Callable[[int], tuple[str, dict[str, object]]] | None = None,
    ) -> int: ...

    def list_contract_versions(
        self, oid: int, dataset_id: int
    ) -> list[SemanticContractVersion]: ...

    def list_workspace_contract_versions(
        self,
        oid: int,
    ) -> list[SemanticContractVersion]: ...

    def list_dimension_hierarchies(
        self, oid: int, domain_id: int | None = None
    ) -> list[DimensionHierarchy]: ...

    def get_dimension_hierarchy(
        self, oid: int, hierarchy_id: int
    ) -> DimensionHierarchy | None: ...

    def list_dimension_hierarchy_levels(
        self, oid: int, hierarchy_id: int
    ) -> list[DimensionHierarchyLevel]: ...

    def create_dimension_hierarchy(
        self,
        hierarchy: DimensionHierarchy,
        levels: list[DimensionHierarchyLevel],
    ) -> DimensionHierarchy: ...

    def update_dimension_hierarchy(
        self,
        hierarchy: DimensionHierarchy,
        levels: list[DimensionHierarchyLevel],
    ) -> DimensionHierarchy: ...

    def delete_dimension_hierarchy(self, hierarchy: DimensionHierarchy) -> None: ...

    def dimension_hierarchy_is_referenced(
        self, oid: int, hierarchy_id: int
    ) -> bool: ...

    def list_metric_relationships(
        self, oid: int, domain_id: int | None = None
    ) -> list[MetricRelationship]: ...

    def get_metric_relationship(
        self, oid: int, relationship_id: int
    ) -> MetricRelationship | None: ...

    def list_metric_relationship_dimensions(
        self, oid: int, relationship_id: int
    ) -> list[MetricRelationshipDimension]: ...

    def create_metric_relationship(
        self,
        relationship: MetricRelationship,
        dimensions: list[MetricRelationshipDimension],
    ) -> MetricRelationship: ...

    def update_metric_relationship(
        self,
        relationship: MetricRelationship,
        dimensions: list[MetricRelationshipDimension],
    ) -> MetricRelationship: ...

    def delete_metric_relationship(self, relationship: MetricRelationship) -> None: ...

    def metric_relationship_is_referenced(
        self, oid: int, relationship_id: int
    ) -> bool: ...

    def metric_reference(self, oid: int, metric_id: int) -> tuple[int, int] | None: ...

    def relation_ids_exist(self, oid: int, relation_ids: list[int]) -> bool: ...
