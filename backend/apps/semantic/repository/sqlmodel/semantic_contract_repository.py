"""基于 SQLModel 的完整语义契约仓储。"""

from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from sqlalchemy import delete, func
from sqlmodel import Session, SQLModel, col, select

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
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticModelRelation,
)
from apps.semantic.models.orm.contract_version import SemanticContractVersion
from apps.semantic.repository.schema_repository import DatasetSchemaAssets
from apps.semantic.repository.semantic_contract_repository import (
    CapabilityReferenceFacts,
    CapabilityRelationReference,
    SemanticContractAssetBundle,
    SemanticContractRepository,
    StoredSemanticContractAssets,
)
from apps.semantic.repository.sqlmodel.results import all_results
from apps.semantic.repository.sqlmodel.storage_sync import (
    mark_domain_datasets_schema_changed,
    sync_dimension_relations,
    sync_metric_relations,
    sync_model_structure,
)

_ContractModel = TypeVar("_ContractModel", bound=SQLModel)


class SqlModelSemanticContractRepository(SemanticContractRepository):
    """阶段1契约对象的 SQLModel 实现。"""

    def __init__(self, session: Session):
        self._session = session

    def list_business_entities(self, oid: int) -> list[BusinessEntity]:
        return all_results(
            self._session.exec(
                select(BusinessEntity)
                .where(BusinessEntity.oid == oid, BusinessEntity.status == 1)
                .order_by(col(BusinessEntity.id))
            )
        )

    def get_business_entity(self, oid: int, entity_id: int) -> BusinessEntity | None:
        entity = self._session.get(BusinessEntity, entity_id)
        if entity is None or entity.oid != oid or entity.status != 1:
            return None
        return entity

    def create_business_entity(self, entity: BusinessEntity) -> BusinessEntity:
        return self._create(entity)

    def update_business_entity(self, entity: BusinessEntity) -> BusinessEntity:
        return self._update(entity)

    def delete_business_entity(self, entity: BusinessEntity) -> None:
        self._delete(entity)

    def list_logical_dimensions(
        self, oid: int, domain_id: int | None = None
    ) -> list[LogicalDimension]:
        statement = select(LogicalDimension).where(
            LogicalDimension.oid == oid,
            LogicalDimension.status == 1,
        )
        if domain_id is not None:
            statement = statement.where(LogicalDimension.domain_id == domain_id)
        return all_results(
            self._session.exec(statement.order_by(col(LogicalDimension.id)))
        )

    def get_logical_dimension(
        self, oid: int, dimension_id: int
    ) -> LogicalDimension | None:
        dimension = self._session.get(LogicalDimension, dimension_id)
        if dimension is None or dimension.oid != oid or dimension.status != 1:
            return None
        return dimension

    def create_logical_dimension(self, dimension: LogicalDimension) -> LogicalDimension:
        return self._create(dimension)

    def update_logical_dimension(self, dimension: LogicalDimension) -> LogicalDimension:
        return self._update(dimension)

    def delete_logical_dimension(self, dimension: LogicalDimension) -> None:
        self._delete(dimension)

    def list_metric_dimension_capabilities(
        self,
        oid: int,
        metric_id: int | None = None,
    ) -> list[MetricDimensionCapability]:
        statement = select(MetricDimensionCapability).where(
            MetricDimensionCapability.oid == oid,
            MetricDimensionCapability.status == 1,
        )
        if metric_id is not None:
            statement = statement.where(
                MetricDimensionCapability.metric_id == metric_id
            )
        return all_results(
            self._session.exec(statement.order_by(col(MetricDimensionCapability.id)))
        )

    def get_metric_dimension_capability(
        self,
        oid: int,
        capability_id: int,
    ) -> MetricDimensionCapability | None:
        capability = self._session.get(MetricDimensionCapability, capability_id)
        if capability is None or capability.oid != oid or capability.status != 1:
            return None
        return capability

    def create_metric_dimension_capability(
        self,
        capability: MetricDimensionCapability,
    ) -> MetricDimensionCapability:
        stored = self._create(capability)
        self.invalidate_published_contracts_for_capability(stored.oid, stored)
        self._session.commit()
        return stored

    def update_metric_dimension_capability(
        self,
        capability: MetricDimensionCapability,
    ) -> MetricDimensionCapability:
        self.invalidate_published_contracts_for_capability(capability.oid, capability)
        return self._update(capability)

    def delete_metric_dimension_capability(
        self,
        capability: MetricDimensionCapability,
    ) -> None:
        self._delete(capability)

    def metric_dimension_capability_is_referenced(
        self,
        oid: int,
        capability_id: int,
    ) -> bool:
        """能力被已发布数据集使用时禁止直接删除。"""

        capability = self._session.get(MetricDimensionCapability, capability_id)
        if capability is None or capability.oid != oid or capability.status != 1:
            return False
        metric = self._session.get(SemanticMetric, capability.metric_id)
        if metric is None or metric.oid != oid or metric.status != 1:
            return False
        active_datasets = all_results(
            self._session.exec(
                select(SemanticDataset).where(
                    SemanticDataset.oid == oid,
                    SemanticDataset.status == 1,
                )
            )
        )
        dataset_ids = {item.id for item in active_datasets if item.id is not None}
        if not dataset_ids:
            return False
        if (
            self._session.exec(
                select(SemanticDatasetAsset.id).where(
                    SemanticDatasetAsset.oid == oid,
                    col(SemanticDatasetAsset.dataset_id).in_(dataset_ids),
                    SemanticDatasetAsset.asset_type == "METRIC",
                    SemanticDatasetAsset.asset_id == metric.id,
                    SemanticDatasetAsset.status == 1,
                )
            ).first()
            is not None
        ):
            return True
        return (
            self._session.exec(
                select(SemanticDatasetModelConfig.id).where(
                    SemanticDatasetModelConfig.oid == oid,
                    col(SemanticDatasetModelConfig.dataset_id).in_(dataset_ids),
                    SemanticDatasetModelConfig.model_id == metric.model_id,
                    col(SemanticDatasetModelConfig.includes_all).is_(True),
                    SemanticDatasetModelConfig.status == 1,
                )
            ).first()
            is not None
        )

    def invalidate_published_contracts_for_capability(
        self,
        oid: int,
        capability: MetricDimensionCapability,
    ) -> None:
        """指标维度能力变化后使包含该指标的数据集契约失效。"""

        metric = self._session.get(SemanticMetric, capability.metric_id)
        if metric is None or metric.oid != oid or metric.status != 1:
            return
        model = self._session.get(SemanticModel, metric.model_id)
        if model is None or model.oid != oid or model.status != 1:
            return
        mark_domain_datasets_schema_changed(self._session, oid, model.domain_id)

    def list_dimension_hierarchies(
        self, oid: int, domain_id: int | None = None
    ) -> list[DimensionHierarchy]:
        statement = select(DimensionHierarchy).where(
            DimensionHierarchy.oid == oid,
            DimensionHierarchy.status == 1,
        )
        if domain_id is not None:
            statement = statement.where(DimensionHierarchy.domain_id == domain_id)
        return all_results(
            self._session.exec(statement.order_by(col(DimensionHierarchy.id)))
        )

    def get_dimension_hierarchy(
        self, oid: int, hierarchy_id: int
    ) -> DimensionHierarchy | None:
        hierarchy = self._session.get(DimensionHierarchy, hierarchy_id)
        if hierarchy is None or hierarchy.oid != oid or hierarchy.status != 1:
            return None
        return hierarchy

    def list_dimension_hierarchy_levels(
        self, oid: int, hierarchy_id: int
    ) -> list[DimensionHierarchyLevel]:
        hierarchy = self.get_dimension_hierarchy(oid, hierarchy_id)
        if hierarchy is None:
            return []
        return all_results(
            self._session.exec(
                select(DimensionHierarchyLevel)
                .where(DimensionHierarchyLevel.hierarchy_id == hierarchy_id)
                .order_by(col(DimensionHierarchyLevel.level_order))
            )
        )

    def create_dimension_hierarchy(
        self,
        hierarchy: DimensionHierarchy,
        levels: list[DimensionHierarchyLevel],
    ) -> DimensionHierarchy:
        self._session.add(hierarchy)
        self._session.flush()
        if hierarchy.id is None:
            raise ValueError("SEMANTIC_HIERARCHY_NOT_PERSISTED")
        for level in levels:
            level.hierarchy_id = hierarchy.id
            self._session.add(level)
        mark_domain_datasets_schema_changed(
            self._session, hierarchy.oid, hierarchy.domain_id
        )
        self._session.commit()
        self._session.refresh(hierarchy)
        return hierarchy

    def update_dimension_hierarchy(
        self,
        hierarchy: DimensionHierarchy,
        levels: list[DimensionHierarchyLevel],
    ) -> DimensionHierarchy:
        if hierarchy.id is None:
            raise ValueError("SEMANTIC_HIERARCHY_NOT_PERSISTED")
        self._session.add(hierarchy)
        self._session.exec(
            delete(DimensionHierarchyLevel).where(
                col(DimensionHierarchyLevel.hierarchy_id) == hierarchy.id
            )
        )
        for level in levels:
            level.id = None
            level.hierarchy_id = hierarchy.id
            self._session.add(level)
        mark_domain_datasets_schema_changed(
            self._session, hierarchy.oid, hierarchy.domain_id
        )
        self._session.commit()
        self._session.refresh(hierarchy)
        return hierarchy

    def delete_dimension_hierarchy(self, hierarchy: DimensionHierarchy) -> None:
        hierarchy.status = 0
        self._session.add(hierarchy)
        mark_domain_datasets_schema_changed(
            self._session, hierarchy.oid, hierarchy.domain_id
        )
        self._session.commit()

    def dimension_hierarchy_is_referenced(self, oid: int, hierarchy_id: int) -> bool:
        return (
            self._session.exec(
                select(SemanticDatasetAsset.id).where(
                    SemanticDatasetAsset.oid == oid,
                    SemanticDatasetAsset.asset_type == "DIMENSION_HIERARCHY",
                    SemanticDatasetAsset.asset_id == hierarchy_id,
                    SemanticDatasetAsset.status == 1,
                )
            ).first()
            is not None
        )

    def list_metric_relationships(
        self, oid: int, domain_id: int | None = None
    ) -> list[MetricRelationship]:
        statement = select(MetricRelationship).where(
            MetricRelationship.oid == oid,
            MetricRelationship.status == 1,
        )
        if domain_id is not None:
            statement = statement.where(MetricRelationship.domain_id == domain_id)
        return all_results(
            self._session.exec(statement.order_by(col(MetricRelationship.id)))
        )

    def get_metric_relationship(
        self, oid: int, relationship_id: int
    ) -> MetricRelationship | None:
        relationship = self._session.get(MetricRelationship, relationship_id)
        if relationship is None or relationship.oid != oid or relationship.status != 1:
            return None
        return relationship

    def list_metric_relationship_dimensions(
        self, oid: int, relationship_id: int
    ) -> list[MetricRelationshipDimension]:
        if self.get_metric_relationship(oid, relationship_id) is None:
            return []
        return all_results(
            self._session.exec(
                select(MetricRelationshipDimension)
                .where(MetricRelationshipDimension.relationship_id == relationship_id)
                .order_by(col(MetricRelationshipDimension.id))
            )
        )

    def create_metric_relationship(
        self,
        relationship: MetricRelationship,
        dimensions: list[MetricRelationshipDimension],
    ) -> MetricRelationship:
        self._session.add(relationship)
        self._session.flush()
        if relationship.id is None:
            raise ValueError("SEMANTIC_METRIC_RELATIONSHIP_NOT_PERSISTED")
        for dimension in dimensions:
            dimension.relationship_id = relationship.id
            self._session.add(dimension)
        mark_domain_datasets_schema_changed(
            self._session, relationship.oid, relationship.domain_id
        )
        self._session.commit()
        self._session.refresh(relationship)
        return relationship

    def update_metric_relationship(
        self,
        relationship: MetricRelationship,
        dimensions: list[MetricRelationshipDimension],
    ) -> MetricRelationship:
        if relationship.id is None:
            raise ValueError("SEMANTIC_METRIC_RELATIONSHIP_NOT_PERSISTED")
        self._session.add(relationship)
        self._session.exec(
            delete(MetricRelationshipDimension).where(
                col(MetricRelationshipDimension.relationship_id) == relationship.id
            )
        )
        for dimension in dimensions:
            dimension.id = None
            dimension.relationship_id = relationship.id
            self._session.add(dimension)
        mark_domain_datasets_schema_changed(
            self._session, relationship.oid, relationship.domain_id
        )
        self._session.commit()
        self._session.refresh(relationship)
        return relationship

    def delete_metric_relationship(self, relationship: MetricRelationship) -> None:
        relationship.status = 0
        self._session.add(relationship)
        mark_domain_datasets_schema_changed(
            self._session, relationship.oid, relationship.domain_id
        )
        self._session.commit()

    def metric_relationship_is_referenced(self, oid: int, relationship_id: int) -> bool:
        return (
            self._session.exec(
                select(SemanticDatasetAsset.id).where(
                    SemanticDatasetAsset.oid == oid,
                    SemanticDatasetAsset.asset_type == "METRIC_RELATIONSHIP",
                    SemanticDatasetAsset.asset_id == relationship_id,
                    SemanticDatasetAsset.status == 1,
                )
            ).first()
            is not None
        )

    def metric_reference(self, oid: int, metric_id: int) -> tuple[int, int] | None:
        metric = self._session.get(SemanticMetric, metric_id)
        if (
            metric is None
            or metric.oid != oid
            or metric.status != 1
            or metric.model_id is None
        ):
            return None
        return metric.model_id, self._metric_domain_id(metric.model_id)

    def relation_ids_exist(self, oid: int, relation_ids: list[int]) -> bool:
        if not relation_ids:
            return True
        rows = all_results(
            self._session.exec(
                select(SemanticModelRelation.id).where(
                    SemanticModelRelation.oid == oid,
                    col(SemanticModelRelation.id).in_(relation_ids),
                    SemanticModelRelation.status == 1,
                )
            )
        )
        return len(rows) == len(set(relation_ids))

    def _metric_domain_id(self, model_id: int) -> int:
        model = self._session.get(SemanticModel, model_id)
        return model.domain_id if model is not None else 0

    def create_contract_assets(
        self,
        bundle: SemanticContractAssetBundle,
    ) -> StoredSemanticContractAssets:
        """在一个事务内保存模型及其完整语义契约。"""

        model_assets = bundle.model_assets
        self._session.add(model_assets.model)
        self._session.flush()
        if model_assets.model.id is None:
            raise ValueError("SEMANTIC_MODEL_NOT_PERSISTED")

        for entity in bundle.business_entities.values():
            self._session.add(entity)
        self._session.flush()

        for reference, logical_dimension in bundle.logical_dimensions.items():
            entity_reference = bundle.logical_dimension_entity_references.get(reference)
            if entity_reference:
                entity = bundle.business_entities[entity_reference]
                if entity.id is None:
                    raise ValueError("SEMANTIC_BUSINESS_ENTITY_NOT_PERSISTED")
                logical_dimension.entity_id = entity.id
            self._session.add(logical_dimension)
        self._session.flush()

        dimensions_by_biz_name: dict[str, SemanticDimension] = {}
        for physical_dimension in model_assets.dimensions:
            physical_dimension.model_id = model_assets.model.id
            self._session.add(physical_dimension)
            dimensions_by_biz_name[physical_dimension.biz_name] = physical_dimension
        metrics_by_biz_name: dict[str, SemanticMetric] = {}
        for metric in model_assets.metrics:
            metric.model_id = model_assets.model.id
            self._session.add(metric)
            metrics_by_biz_name[metric.biz_name] = metric
        self._session.flush()

        for binding in bundle.dimension_bindings:
            physical_dimension = dimensions_by_biz_name[binding.dimension_biz_name]
            logical_dimension_id = binding.logical_dimension_id
            if binding.logical_dimension_reference:
                logical_dimension = bundle.logical_dimensions[
                    binding.logical_dimension_reference
                ]
                logical_dimension_id = logical_dimension.id
            physical_dimension.logical_dimension_id = logical_dimension_id
            physical_dimension.binding_role = binding.binding_role
            physical_dimension.binding_priority = binding.binding_priority
            self._session.add(physical_dimension)

        for (
            metric_biz_name,
            dimension_biz_name,
        ) in bundle.metric_default_time_dimensions.items():
            metric = metrics_by_biz_name[metric_biz_name]
            metric.default_time_dimension_id = dimensions_by_biz_name[
                dimension_biz_name
            ].id
            self._session.add(metric)

        capabilities = []
        for pending in bundle.capabilities:
            metric = metrics_by_biz_name[pending.metric_biz_name]
            physical_dimension = dimensions_by_biz_name[
                pending.physical_dimension_biz_name
            ]
            logical_dimension_id = pending.logical_dimension_id
            if pending.logical_dimension_reference:
                logical_dimension_id = bundle.logical_dimensions[
                    pending.logical_dimension_reference
                ].id
            if metric.id is None or physical_dimension.id is None:
                raise ValueError("SEMANTIC_CONTRACT_ASSET_NOT_PERSISTED")
            capability = MetricDimensionCapability(
                oid=model_assets.model.oid,
                metric_id=metric.id,
                logical_dimension_id=logical_dimension_id,
                usages=pending.usages,
                binding_strategy="SAME_MODEL",
                relation_path=[],
                target_model_id=model_assets.model.id,
                physical_dimension_id=physical_dimension.id,
                aggregation_safety=pending.aggregation_safety,
                pre_aggregation_grain=pending.pre_aggregation_grain,
                time_alignment_policy=pending.time_alignment_policy,
                contribution_tolerance=pending.contribution_tolerance,
            )
            self._session.add(capability)
            capabilities.append(capability)

        sync_model_structure(self._session, model_assets.model)
        for physical_dimension in model_assets.dimensions:
            sync_dimension_relations(self._session, physical_dimension)
        for metric in model_assets.metrics:
            sync_metric_relations(self._session, metric)
        self._session.commit()
        self._session.refresh(model_assets.model)
        return StoredSemanticContractAssets(
            model_assets=model_assets,
            business_entities=bundle.business_entities,
            logical_dimensions=bundle.logical_dimensions,
            capabilities=capabilities,
        )

    def capability_reference_facts(
        self,
        oid: int,
        metric_id: int,
        logical_dimension_id: int,
        target_model_id: int,
        physical_dimension_id: int | None,
        relation_path: list[int],
    ) -> CapabilityReferenceFacts:
        metric = self._session.get(SemanticMetric, metric_id)
        if metric is not None and (metric.oid != oid or metric.status != 1):
            metric = None
        logical_dimension = self.get_logical_dimension(oid, logical_dimension_id)
        target_model = self._session.get(SemanticModel, target_model_id)
        if target_model is not None and (
            target_model.oid != oid or target_model.status != 1
        ):
            target_model = None
        physical_dimension = (
            self._session.get(SemanticDimension, physical_dimension_id)
            if physical_dimension_id is not None
            else None
        )
        if physical_dimension is not None and (
            physical_dimension.oid != oid or physical_dimension.status != 1
        ):
            physical_dimension = None
        relations = []
        for relation_id in relation_path:
            relation = self._session.get(SemanticModelRelation, relation_id)
            if relation is None or relation.oid != oid or relation.status != 1:
                continue
            relations.append(
                CapabilityRelationReference(
                    relation_id=relation_id,
                    left_model_id=relation.left_model_id,
                    right_model_id=relation.right_model_id,
                )
            )
        return CapabilityReferenceFacts(
            metric_model_id=metric.model_id if metric else None,
            logical_dimension_domain_id=(
                logical_dimension.domain_id if logical_dimension else None
            ),
            target_model_domain_id=target_model.domain_id if target_model else None,
            physical_dimension_model_id=(
                physical_dimension.model_id if physical_dimension else None
            ),
            physical_dimension_logical_id=(
                physical_dimension.logical_dimension_id if physical_dimension else None
            ),
            relations=tuple(relations),
        )

    def business_entity_is_referenced(self, oid: int, entity_id: int) -> bool:
        return (
            self._session.exec(
                select(LogicalDimension.id).where(
                    LogicalDimension.oid == oid,
                    LogicalDimension.entity_id == entity_id,
                    LogicalDimension.status == 1,
                )
            ).first()
            is not None
        )

    def logical_dimension_is_referenced(
        self, oid: int, logical_dimension_id: int
    ) -> bool:
        dimension_id = self._session.exec(
            select(SemanticDimension.id).where(
                SemanticDimension.oid == oid,
                SemanticDimension.logical_dimension_id == logical_dimension_id,
                SemanticDimension.status == 1,
            )
        ).first()
        capability_id = self._session.exec(
            select(MetricDimensionCapability.id).where(
                MetricDimensionCapability.oid == oid,
                MetricDimensionCapability.logical_dimension_id == logical_dimension_id,
                MetricDimensionCapability.status == 1,
            )
        ).first()
        hierarchy_level_id = self._session.exec(
            select(DimensionHierarchyLevel.id).where(
                DimensionHierarchyLevel.logical_dimension_id == logical_dimension_id
            )
        ).first()
        relationship_dimension_id = self._session.exec(
            select(MetricRelationshipDimension.id).where(
                MetricRelationshipDimension.logical_dimension_id == logical_dimension_id
            )
        ).first()
        return (
            dimension_id is not None
            or capability_id is not None
            or hierarchy_level_id is not None
            or relationship_dimension_id is not None
        )

    def publish_contract(
        self,
        assets: DatasetSchemaAssets,
        *,
        contract_version: int | None = None,
        schema_fingerprint: str = "",
        asset_snapshot: dict[str, object] | None = None,
        published_by: int | None = None,
        build_snapshot: Callable[[int], tuple[str, dict[str, object]]] | None = None,
    ) -> int:
        """在同一事务中写入契约版本和不可变发布快照。"""

        dataset_id = assets.dataset.id
        if dataset_id is None:
            raise ValueError("SEMANTIC_DATASET_NOT_PERSISTED")
        # 锁定数据集后再读取历史，版本计算和后续发布写入处于同一事务。
        locked_dataset = self._session.exec(
            select(SemanticDataset)
            .where(
                SemanticDataset.oid == assets.dataset.oid,
                SemanticDataset.id == dataset_id,
                SemanticDataset.status == 1,
            )
            .with_for_update()
        ).one_or_none()
        if locked_dataset is None:
            raise ValueError("SEMANTIC_DATASET_NOT_FOUND")
        # SQLModel Session 会复用同一数据集实体；DatasetSchemaAssets 是不可变容器，
        # 不能替换其中的 dataset 引用，但后续字段更新仍在锁定的同一行上执行。
        latest_history = self._session.exec(
            select(func.max(SemanticContractVersion.contract_version)).where(
                SemanticContractVersion.oid == locked_dataset.oid,
                SemanticContractVersion.dataset_id == dataset_id,
            )
        ).one()
        latest_version = int(latest_history or 0)
        # 版本历史是唯一递增来源；数据集当前版本可能因草稿失效被清零。
        contract_version = latest_version + 1

        if build_snapshot is not None:
            schema_fingerprint, asset_snapshot = build_snapshot(contract_version)
        elif not schema_fingerprint or asset_snapshot is None:
            raise ValueError("SEMANTIC_CONTRACT_SNAPSHOT_REQUIRED")

        assets.dataset.contract_version = contract_version
        self._session.add(assets.dataset)
        for model in assets.models:
            model.contract_status = "READY"
            model.contract_version = contract_version
            self._session.add(model)
        for relation in assets.model_relations:
            relation.contract_status = "READY"
            relation.contract_version = contract_version
            self._session.add(relation)
        for metric in assets.metrics:
            metric.contract_version = contract_version
            self._session.add(metric)
        for dimension in assets.dimensions:
            dimension.contract_version = contract_version
            self._session.add(dimension)
        for hierarchy in assets.dimension_hierarchies:
            hierarchy.contract_status = "CERTIFIED"
            hierarchy.version = max(hierarchy.version, 1)
            self._session.add(hierarchy)
        for relationship in assets.metric_relationships:
            relationship.contract_status = "CERTIFIED"
            relationship.version = max(relationship.version, 1)
            self._session.add(relationship)
        self._session.add(
            SemanticContractVersion(
                oid=assets.dataset.oid,
                dataset_id=assets.dataset.id or 0,
                schema_version=assets.dataset.schema_version,
                contract_version=contract_version,
                schema_fingerprint=schema_fingerprint,
                asset_snapshot=asset_snapshot or {},
                published_by=published_by,
                published_at=datetime.now(),
            )
        )
        self._session.commit()
        return contract_version

    def list_contract_versions(
        self, oid: int, dataset_id: int
    ) -> list[SemanticContractVersion]:
        return all_results(
            self._session.exec(
                select(SemanticContractVersion)
                .where(
                    SemanticContractVersion.oid == oid,
                    SemanticContractVersion.dataset_id == dataset_id,
                )
                .order_by(col(SemanticContractVersion.contract_version).desc())
            )
        )

    def list_workspace_contract_versions(
        self,
        oid: int,
    ) -> list[SemanticContractVersion]:
        return all_results(
            self._session.exec(
                select(SemanticContractVersion)
                .where(SemanticContractVersion.oid == oid)
                .order_by(
                    col(SemanticContractVersion.dataset_id),
                    col(SemanticContractVersion.contract_version).desc(),
                )
            )
        )

    def _create(self, entity: _ContractModel) -> _ContractModel:
        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return entity

    def _update(self, entity: _ContractModel) -> _ContractModel:
        domain_id = getattr(entity, "domain_id", None)
        if isinstance(domain_id, int):
            oid = getattr(entity, "oid", None)
            if isinstance(oid, int):
                mark_domain_datasets_schema_changed(self._session, oid, domain_id)
        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return entity

    def _delete(self, entity: SQLModel) -> None:
        domain_id = getattr(entity, "domain_id", None)
        if isinstance(domain_id, int):
            oid = getattr(entity, "oid", None)
            if isinstance(oid, int):
                mark_domain_datasets_schema_changed(self._session, oid, domain_id)
        self._session.delete(entity)
        self._session.commit()
