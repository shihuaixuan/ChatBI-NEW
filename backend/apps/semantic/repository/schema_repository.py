from dataclasses import dataclass, field
from typing import Protocol

from apps.datasource import DatasourceRecord
from apps.semantic.models.orm import (
    BusinessEntity,
    LogicalDimension,
    MetricDimensionCapability,
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
    metric_dimension_capabilities: list[MetricDimensionCapability] = field(default_factory=list)
    instructions: list[SemanticDatasetInstruction] = field(default_factory=list)


class SchemaRepository(Protocol):
    """数据集语义结构所需资产的仓储端口。"""

    def load(self, oid: int, dataset_id: int) -> DatasetSchemaAssets: ...
