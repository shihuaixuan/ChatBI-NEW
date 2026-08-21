from dataclasses import dataclass, field
from typing import Any, Protocol

from apps.datasource import DatasourceRecord
from apps.semantic.models.orm import (
    BusinessEntity,
    DimensionHierarchy,
    DimensionHierarchyLevel,
    LogicalDimension,
    MetricDimensionCapability,
    MetricRelationship,
    MetricRelationshipDimension,
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetInstruction,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticDomain,
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelMeasure,
    SemanticModelRelation,
    SemanticTerm,
)


@dataclass(frozen=True, slots=True)
class DatasetSchemaAssets:
    """构建数据集 Schema 所需的完整语义资产快照。"""

    dataset: SemanticDataset
    domain: SemanticDomain | None
    models: list[SemanticModel]
    metrics: list[SemanticMetric]
    dimensions: list[SemanticDimension]
    terms: list[SemanticTerm]
    datasources: list[DatasourceRecord]
    model_relations: list[SemanticModelRelation]
    model_fields: list[SemanticModelField]
    model_measures: list[SemanticModelMeasure]
    dimension_values: list[SemanticDimensionValue]
    dataset_model_configs: list[SemanticDatasetModelConfig]
    dataset_assets: list[SemanticDatasetAsset]
    subject_domains: list[SemanticDomain]
    business_entities: list[BusinessEntity] = field(default_factory=list)
    logical_dimensions: list[LogicalDimension] = field(default_factory=list)
    metric_dimension_capabilities: list[MetricDimensionCapability] = field(
        default_factory=list
    )
    dimension_hierarchies: list[DimensionHierarchy] = field(default_factory=list)
    dimension_hierarchy_levels: list[DimensionHierarchyLevel] = field(
        default_factory=list
    )
    metric_relationships: list[MetricRelationship] = field(default_factory=list)
    metric_relationship_dimensions: list[MetricRelationshipDimension] = field(
        default_factory=list
    )
    selected_dimension_hierarchy_ids: list[int] = field(default_factory=list)
    selected_metric_relationship_ids: list[int] = field(default_factory=list)
    instructions: list[SemanticDatasetInstruction] = field(default_factory=list)


class SchemaRepository(Protocol):
    """数据集语义结构所需资产的仓储端口。"""

    def load(
        self,
        oid: int,
        dataset_id: int,
        *,
        include_drafts: bool = False,
    ) -> DatasetSchemaAssets: ...

    def load_published_schema(
        self,
        oid: int,
        dataset_id: int,
    ) -> dict[str, Any]: ...
