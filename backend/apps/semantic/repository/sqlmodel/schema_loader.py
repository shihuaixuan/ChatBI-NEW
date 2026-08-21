from __future__ import annotations

from sqlmodel import Session, col, select

from apps.datasource.composition import build_datasource_service
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
from apps.semantic.models.orm.contract_version import SemanticContractVersion
from apps.semantic.repository.schema_repository import DatasetSchemaAssets
from apps.semantic.repository.sqlmodel.results import all_results
from apps.semantic.utils.schema_selection import (
    configured_model_ids,
    selected_model_domain_ids,
)


class SemanticSchemaLoader:
    """从持久化存储加载一次 Schema 构建所需的语义资产。"""

    def __init__(self, session: Session):
        self._session = session

    def load_published_schema(
        self,
        oid: int,
        dataset_id: int,
    ) -> dict[str, object]:
        """读取当前契约版本对应的不可变 DatasetSchema 快照。"""

        dataset = self._session.get(SemanticDataset, dataset_id)
        if dataset is None or dataset.oid != oid or dataset.status != 1:
            raise ValueError("SEMANTIC_DATASET_NOT_FOUND")
        version = self._session.exec(
            select(SemanticContractVersion)
            .where(
                SemanticContractVersion.oid == oid,
                SemanticContractVersion.dataset_id == dataset_id,
            )
            .order_by(col(SemanticContractVersion.contract_version).desc())
            .limit(1)
        ).one_or_none()
        schema = version.asset_snapshot.get("schema") if version is not None else None
        if not isinstance(schema, dict):
            raise ValueError("SEMANTIC_DATASET_PUBLISHED_SCHEMA_MISSING")
        return schema

    def load(
        self,
        oid: int,
        dataset_id: int,
        *,
        include_drafts: bool = False,
    ) -> DatasetSchemaAssets:
        dataset = self._session.get(SemanticDataset, dataset_id)
        if dataset is None or dataset.oid != oid or dataset.status != 1:
            raise ValueError("SEMANTIC_DATASET_NOT_FOUND")
        # 所有运行模式只消费已发布契约；治理和发布校验显式读取草稿。
        if dataset.contract_version <= 0 and not include_drafts:
            raise ValueError("SEMANTIC_DATASET_CONTRACT_NOT_PUBLISHED")

        domain = self._session.get(SemanticDomain, dataset.domain_id)
        dataset_model_configs = all_results(
            self._session.exec(
                select(SemanticDatasetModelConfig).where(
                    SemanticDatasetModelConfig.oid == oid,
                    SemanticDatasetModelConfig.dataset_id == dataset_id,
                    SemanticDatasetModelConfig.status == 1,
                )
            )
        )
        if not dataset_model_configs:
            raise ValueError("SEMANTIC_DATASET_MODEL_CONFIG_MISSING")
        instructions = all_results(
            self._session.exec(
                select(SemanticDatasetInstruction).where(
                    SemanticDatasetInstruction.oid == oid,
                    SemanticDatasetInstruction.dataset_id == dataset_id,
                    col(SemanticDatasetInstruction.enabled).is_(True),
                )
            )
        )
        dataset_assets = all_results(
            self._session.exec(
                select(SemanticDatasetAsset).where(
                    SemanticDatasetAsset.oid == oid,
                    SemanticDatasetAsset.dataset_id == dataset_id,
                    SemanticDatasetAsset.status == 1,
                )
            )
        )
        if not dataset_assets and not any(
            config.includes_all for config in dataset_model_configs
        ):
            raise ValueError("SEMANTIC_DATASET_ASSET_MISSING")
        selected_hierarchy_ids = [
            item.asset_id
            for item in dataset_assets
            if item.asset_type == "DIMENSION_HIERARCHY"
        ]
        selected_relationship_ids = [
            item.asset_id
            for item in dataset_assets
            if item.asset_type == "METRIC_RELATIONSHIP"
        ]
        model_ids_in_config = configured_model_ids(dataset, dataset_model_configs)
        models = all_results(
            self._session.exec(
                select(SemanticModel).where(
                    SemanticModel.oid == oid,
                    col(SemanticModel.id).in_(model_ids_in_config),
                    SemanticModel.status == 1,
                )
            )
        )
        model_order = {
            model_id: index for index, model_id in enumerate(model_ids_in_config)
        }
        models.sort(key=lambda model: model_order.get(model.id, len(model_order)))

        model_domain_ids = selected_model_domain_ids(models)
        subject_domains = self._load_subject_domains(oid, model_domain_ids, domain)
        datasource_ids = {
            model.datasource_id for model in models if model.datasource_id is not None
        }
        datasources = [
            datasource
            for datasource in build_datasource_service(self._session).list_by_workspace(
                oid
            )
            if datasource.id in datasource_ids
        ]
        model_ids = [model.id for model in models if model.id is not None]
        model_fields = (
            all_results(
                self._session.exec(
                    select(SemanticModelField).where(
                        SemanticModelField.oid == oid,
                        col(SemanticModelField.model_id).in_(model_ids),
                        SemanticModelField.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        model_measures = (
            all_results(
                self._session.exec(
                    select(SemanticModelMeasure).where(
                        SemanticModelMeasure.oid == oid,
                        col(SemanticModelMeasure.model_id).in_(model_ids),
                        SemanticModelMeasure.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        metrics = (
            all_results(
                self._session.exec(
                    select(SemanticMetric).where(
                        SemanticMetric.oid == oid,
                        col(SemanticMetric.model_id).in_(model_ids),
                        SemanticMetric.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        dimensions = (
            all_results(
                self._session.exec(
                    select(SemanticDimension).where(
                        SemanticDimension.oid == oid,
                        col(SemanticDimension.model_id).in_(model_ids),
                        SemanticDimension.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        dimension_ids = [
            dimension.id for dimension in dimensions if dimension.id is not None
        ]
        dimension_values = (
            all_results(
                self._session.exec(
                    select(SemanticDimensionValue).where(
                        SemanticDimensionValue.oid == oid,
                        col(SemanticDimensionValue.dimension_id).in_(dimension_ids),
                        SemanticDimensionValue.status == 1,
                    )
                )
            )
            if dimension_ids
            else []
        )
        domain_ids = model_domain_ids or [dataset.domain_id]
        terms = all_results(
            self._session.exec(
                select(SemanticTerm).where(
                    SemanticTerm.oid == oid,
                    col(SemanticTerm.domain_id).in_(domain_ids),
                    SemanticTerm.status == 1,
                )
            )
        )
        model_relations = (
            all_results(
                self._session.exec(
                    select(SemanticModelRelation).where(
                        SemanticModelRelation.oid == oid,
                        col(SemanticModelRelation.domain_id).in_(domain_ids),
                        SemanticModelRelation.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        business_entities = all_results(
            self._session.exec(
                select(BusinessEntity).where(
                    BusinessEntity.oid == oid,
                    col(BusinessEntity.domain_id).in_(domain_ids),
                    BusinessEntity.status == 1,
                )
            )
        )
        logical_dimensions = all_results(
            self._session.exec(
                select(LogicalDimension).where(
                    LogicalDimension.oid == oid,
                    col(LogicalDimension.domain_id).in_(domain_ids),
                    LogicalDimension.status == 1,
                )
            )
        )
        metric_ids = [metric.id for metric in metrics if metric.id is not None]
        metric_dimension_capabilities = (
            all_results(
                self._session.exec(
                    select(MetricDimensionCapability).where(
                        MetricDimensionCapability.oid == oid,
                        col(MetricDimensionCapability.metric_id).in_(metric_ids),
                        MetricDimensionCapability.status == 1,
                    )
                )
            )
            if metric_ids
            else []
        )
        dimension_hierarchies = (
            all_results(
                self._session.exec(
                    select(DimensionHierarchy).where(
                        DimensionHierarchy.oid == oid,
                        col(DimensionHierarchy.id).in_(selected_hierarchy_ids),
                        DimensionHierarchy.status == 1,
                    )
                )
            )
            if selected_hierarchy_ids
            else []
        )
        hierarchy_ids = [
            item.id for item in dimension_hierarchies if item.id is not None
        ]
        dimension_hierarchy_levels = (
            all_results(
                self._session.exec(
                    select(DimensionHierarchyLevel).where(
                        col(DimensionHierarchyLevel.hierarchy_id).in_(hierarchy_ids)
                    )
                )
            )
            if hierarchy_ids
            else []
        )
        metric_relationships = (
            all_results(
                self._session.exec(
                    select(MetricRelationship).where(
                        MetricRelationship.oid == oid,
                        col(MetricRelationship.id).in_(selected_relationship_ids),
                        MetricRelationship.status == 1,
                    )
                )
            )
            if selected_relationship_ids
            else []
        )
        relationship_ids = [
            item.id for item in metric_relationships if item.id is not None
        ]
        metric_relationship_dimensions = (
            all_results(
                self._session.exec(
                    select(MetricRelationshipDimension).where(
                        col(MetricRelationshipDimension.relationship_id).in_(
                            relationship_ids
                        )
                    )
                )
            )
            if relationship_ids
            else []
        )
        return DatasetSchemaAssets(
            dataset=dataset,
            domain=domain,
            models=models,
            metrics=metrics,
            dimensions=dimensions,
            terms=terms,
            datasources=datasources,
            model_relations=model_relations,
            model_fields=model_fields,
            model_measures=model_measures,
            dimension_values=dimension_values,
            dataset_model_configs=dataset_model_configs,
            dataset_assets=dataset_assets,
            subject_domains=subject_domains,
            business_entities=business_entities,
            logical_dimensions=logical_dimensions,
            metric_dimension_capabilities=metric_dimension_capabilities,
            dimension_hierarchies=dimension_hierarchies,
            dimension_hierarchy_levels=dimension_hierarchy_levels,
            metric_relationships=metric_relationships,
            metric_relationship_dimensions=metric_relationship_dimensions,
            selected_dimension_hierarchy_ids=selected_hierarchy_ids,
            selected_metric_relationship_ids=selected_relationship_ids,
            instructions=instructions,
        )

    def _load_subject_domains(
        self,
        oid: int,
        domain_ids: list[int],
        fallback_domain: SemanticDomain | None,
    ) -> list[SemanticDomain]:
        domains: list[SemanticDomain] = []
        seen: set[int] = set()
        for domain_id in domain_ids:
            if domain_id in seen:
                continue
            seen.add(domain_id)
            domain = (
                fallback_domain
                if fallback_domain is not None and fallback_domain.id == domain_id
                else None
            )
            if domain is None:
                domain = self._session.get(SemanticDomain, domain_id)
            if domain is not None and domain.oid == oid and domain.status == 1:
                domains.append(domain)
        return domains
