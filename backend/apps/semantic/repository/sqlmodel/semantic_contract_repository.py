"""基于 SQLModel 的完整语义契约仓储。"""

from typing import TypeVar

from sqlmodel import Session, SQLModel, col, select

from apps.semantic.models.orm import (
    BusinessEntity,
    LogicalDimension,
    MetricDimensionCapability,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticModelRelation,
)
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

    def list_logical_dimensions(self, oid: int, domain_id: int | None = None) -> list[LogicalDimension]:
        statement = select(LogicalDimension).where(
            LogicalDimension.oid == oid,
            LogicalDimension.status == 1,
        )
        if domain_id is not None:
            statement = statement.where(LogicalDimension.domain_id == domain_id)
        return all_results(self._session.exec(statement.order_by(col(LogicalDimension.id))))

    def get_logical_dimension(self, oid: int, dimension_id: int) -> LogicalDimension | None:
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
            statement = statement.where(MetricDimensionCapability.metric_id == metric_id)
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
        return self._create(capability)

    def update_metric_dimension_capability(
        self,
        capability: MetricDimensionCapability,
    ) -> MetricDimensionCapability:
        return self._update(capability)

    def delete_metric_dimension_capability(
        self,
        capability: MetricDimensionCapability,
    ) -> None:
        self._delete(capability)

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

        for metric_biz_name, dimension_biz_name in (
            bundle.metric_default_time_dimensions.items()
        ):
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
                physical_dimension.logical_dimension_id
                if physical_dimension
                else None
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
                MetricDimensionCapability.logical_dimension_id
                == logical_dimension_id,
                MetricDimensionCapability.status == 1,
            )
        ).first()
        return dimension_id is not None or capability_id is not None

    def publish_contract(
        self,
        assets: DatasetSchemaAssets,
        contract_version: int,
    ) -> None:
        """在同一事务中写入数据集所选资产的契约版本。"""

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
        self._session.commit()

    def _create(self, entity: _ContractModel) -> _ContractModel:
        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return entity

    def _update(self, entity: _ContractModel) -> _ContractModel:
        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return entity

    def _delete(self, entity: SQLModel) -> None:
        self._session.delete(entity)
        self._session.commit()
