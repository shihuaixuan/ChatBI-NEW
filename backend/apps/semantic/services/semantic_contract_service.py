"""完整语义契约的基础管理服务。"""

from apps.semantic.errors import SemanticNotFoundError, SemanticValidationError
from apps.semantic.models.dto import (
    BusinessEntityPayload,
    DimensionHierarchyDTO,
    DimensionHierarchyLevelDTO,
    DimensionHierarchyPayload,
    LogicalDimensionPayload,
    MetricDimensionCapabilityPayload,
    MetricRelationshipDTO,
    MetricRelationshipPayload,
)
from apps.semantic.models.orm import (
    BusinessEntity,
    DimensionHierarchy,
    DimensionHierarchyLevel,
    LogicalDimension,
    MetricDimensionCapability,
    MetricRelationship,
    MetricRelationshipDimension,
)
from apps.semantic.repository.domain_repository import DomainRepository
from apps.semantic.repository.semantic_contract_repository import (
    CapabilityReferenceFacts,
    SemanticContractRepository,
)
from apps.semantic.utils.model_update import assign_values


class SemanticContractService:
    """阶段1契约对象的保存、读取和停用服务。"""

    def __init__(
        self,
        repository: SemanticContractRepository,
        domain_reader: DomainRepository,
    ):
        self._repository = repository
        self._domain_reader = domain_reader

    def list_business_entities(self, oid: int) -> list[BusinessEntity]:
        return self._repository.list_business_entities(oid)

    def create_business_entity(self, oid: int, payload: BusinessEntityPayload) -> BusinessEntity:
        self._ensure_domain(oid, payload.domain_id)
        return self._repository.create_business_entity(
            BusinessEntity(**payload.model_dump(), oid=oid)
        )

    def update_business_entity(
        self,
        oid: int,
        entity_id: int,
        payload: BusinessEntityPayload,
    ) -> BusinessEntity:
        entity = self._require_entity(oid, entity_id)
        self._ensure_domain(oid, payload.domain_id)
        if (
            entity.domain_id != payload.domain_id
            and self._repository.business_entity_is_referenced(oid, entity_id)
        ):
            raise SemanticValidationError(
                "SEMANTIC_BUSINESS_ENTITY_DOMAIN_CHANGE_FORBIDDEN"
            )
        assign_values(entity, payload.model_dump())
        return self._repository.update_business_entity(entity)

    def delete_business_entity(self, oid: int, entity_id: int) -> dict[str, int | bool]:
        entity = self._require_entity(oid, entity_id)
        if self._repository.business_entity_is_referenced(oid, entity_id):
            raise SemanticValidationError("SEMANTIC_BUSINESS_ENTITY_IN_USE")
        self._repository.delete_business_entity(entity)
        return {"id": entity_id, "deleted": True}

    def list_logical_dimensions(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[LogicalDimension]:
        return self._repository.list_logical_dimensions(oid, domain_id)

    def create_logical_dimension(
        self,
        oid: int,
        payload: LogicalDimensionPayload,
    ) -> LogicalDimension:
        self._validate_logical_dimension(oid, payload)
        return self._repository.create_logical_dimension(
            LogicalDimension(**payload.model_dump(), oid=oid)
        )

    def update_logical_dimension(
        self,
        oid: int,
        dimension_id: int,
        payload: LogicalDimensionPayload,
    ) -> LogicalDimension:
        dimension = self._require_logical_dimension(oid, dimension_id)
        self._validate_logical_dimension(oid, payload)
        assign_values(dimension, payload.model_dump())
        return self._repository.update_logical_dimension(dimension)

    def delete_logical_dimension(self, oid: int, dimension_id: int) -> dict[str, int | bool]:
        dimension = self._require_logical_dimension(oid, dimension_id)
        if self._repository.logical_dimension_is_referenced(oid, dimension_id):
            raise SemanticValidationError("SEMANTIC_LOGICAL_DIMENSION_IN_USE")
        self._repository.delete_logical_dimension(dimension)
        return {"id": dimension_id, "deleted": True}

    def list_metric_dimension_capabilities(
        self,
        oid: int,
        metric_id: int | None = None,
    ) -> list[MetricDimensionCapability]:
        return self._repository.list_metric_dimension_capabilities(oid, metric_id)

    def create_metric_dimension_capability(
        self,
        oid: int,
        payload: MetricDimensionCapabilityPayload,
    ) -> MetricDimensionCapability:
        self._validate_capability(oid, payload)
        return self._repository.create_metric_dimension_capability(
            MetricDimensionCapability(**payload.model_dump(), oid=oid)
        )

    def update_metric_dimension_capability(
        self,
        oid: int,
        capability_id: int,
        payload: MetricDimensionCapabilityPayload,
    ) -> MetricDimensionCapability:
        capability = self._require_capability(oid, capability_id)
        self._validate_capability(oid, payload)
        assign_values(capability, payload.model_dump())
        capability.version += 1
        return self._repository.update_metric_dimension_capability(capability)

    def delete_metric_dimension_capability(
        self,
        oid: int,
        capability_id: int,
    ) -> dict[str, int | bool]:
        capability = self._require_capability(oid, capability_id)
        if self._repository.metric_dimension_capability_is_referenced(oid, capability_id):
            raise SemanticValidationError(
                "SEMANTIC_METRIC_DIMENSION_CAPABILITY_IN_USE"
            )
        self._repository.delete_metric_dimension_capability(capability)
        return {"id": capability_id, "deleted": True}

    def list_dimension_hierarchies(
        self, oid: int, domain_id: int | None = None
    ) -> list[DimensionHierarchyDTO]:
        return [
            self._hierarchy_dto(
                hierarchy,
                self._repository.list_dimension_hierarchy_levels(oid, hierarchy.id or 0),
            )
            for hierarchy in self._repository.list_dimension_hierarchies(oid, domain_id)
        ]

    def create_dimension_hierarchy(
        self, oid: int, payload: DimensionHierarchyPayload
    ) -> DimensionHierarchyDTO:
        self._validate_hierarchy(oid, payload)
        hierarchy = DimensionHierarchy(
            oid=oid,
            domain_id=payload.domain_id,
            name=payload.name,
            biz_name=payload.biz_name,
            description=payload.description,
            hierarchy_type=payload.hierarchy_type,
        )
        levels = [DimensionHierarchyLevel(**item.model_dump()) for item in payload.levels]
        stored = self._repository.create_dimension_hierarchy(hierarchy, levels)
        return self._hierarchy_dto(
            stored,
            self._repository.list_dimension_hierarchy_levels(oid, stored.id or 0),
        )

    def update_dimension_hierarchy(
        self, oid: int, hierarchy_id: int, payload: DimensionHierarchyPayload
    ) -> DimensionHierarchyDTO:
        hierarchy = self._require_hierarchy(oid, hierarchy_id)
        self._validate_hierarchy(oid, payload)
        assign_values(
            hierarchy,
            {
                key: value
                for key, value in payload.model_dump().items()
                if key != "levels"
            },
        )
        hierarchy.contract_status = "DRAFT"
        hierarchy.version += 1
        levels = [DimensionHierarchyLevel(**item.model_dump()) for item in payload.levels]
        stored = self._repository.update_dimension_hierarchy(hierarchy, levels)
        return self._hierarchy_dto(
            stored,
            self._repository.list_dimension_hierarchy_levels(oid, hierarchy_id),
        )

    def delete_dimension_hierarchy(self, oid: int, hierarchy_id: int) -> dict[str, int | bool]:
        hierarchy = self._require_hierarchy(oid, hierarchy_id)
        if self._repository.dimension_hierarchy_is_referenced(oid, hierarchy_id):
            raise SemanticValidationError("SEMANTIC_DIMENSION_HIERARCHY_IN_USE")
        self._repository.delete_dimension_hierarchy(hierarchy)
        return {"id": hierarchy_id, "deleted": True}

    def list_metric_relationships(
        self, oid: int, domain_id: int | None = None
    ) -> list[MetricRelationshipDTO]:
        return [
            self._relationship_dto(
                relationship,
                self._repository.list_metric_relationship_dimensions(oid, relationship.id or 0),
            )
            for relationship in self._repository.list_metric_relationships(oid, domain_id)
        ]

    def create_metric_relationship(
        self, oid: int, payload: MetricRelationshipPayload
    ) -> MetricRelationshipDTO:
        self._validate_relationship(oid, payload)
        relationship = MetricRelationship(
            oid=oid,
            domain_id=payload.domain_id,
            target_metric_id=payload.target_metric_id,
            driver_metric_id=payload.driver_metric_id,
            relationship_type=payload.relationship_type,
            validation_method=payload.validation_method,
            expected_direction=payload.expected_direction,
            supported_time_roles=payload.supported_time_roles,
            relation_path=payload.relation_path,
        )
        dimensions = [
            MetricRelationshipDimension(logical_dimension_id=item)
            for item in payload.logical_dimension_ids
        ]
        stored = self._repository.create_metric_relationship(relationship, dimensions)
        return self._relationship_dto(
            stored,
            self._repository.list_metric_relationship_dimensions(oid, stored.id or 0),
        )

    def update_metric_relationship(
        self, oid: int, relationship_id: int, payload: MetricRelationshipPayload
    ) -> MetricRelationshipDTO:
        relationship = self._require_relationship(oid, relationship_id)
        self._validate_relationship(oid, payload)
        assign_values(
            relationship,
            {
                key: value
                for key, value in payload.model_dump().items()
                if key != "logical_dimension_ids"
            },
        )
        relationship.contract_status = "DRAFT"
        relationship.version += 1
        dimensions = [
            MetricRelationshipDimension(logical_dimension_id=item)
            for item in payload.logical_dimension_ids
        ]
        stored = self._repository.update_metric_relationship(relationship, dimensions)
        return self._relationship_dto(
            stored,
            self._repository.list_metric_relationship_dimensions(oid, relationship_id),
        )

    def delete_metric_relationship(self, oid: int, relationship_id: int) -> dict[str, int | bool]:
        relationship = self._require_relationship(oid, relationship_id)
        if self._repository.metric_relationship_is_referenced(oid, relationship_id):
            raise SemanticValidationError("SEMANTIC_METRIC_RELATIONSHIP_IN_USE")
        self._repository.delete_metric_relationship(relationship)
        return {"id": relationship_id, "deleted": True}

    def _require_entity(self, oid: int, entity_id: int) -> BusinessEntity:
        entity = self._repository.get_business_entity(oid, entity_id)
        if entity is None:
            raise SemanticNotFoundError("SEMANTIC_BUSINESS_ENTITY_NOT_FOUND")
        return entity

    def _require_logical_dimension(self, oid: int, dimension_id: int) -> LogicalDimension:
        dimension = self._repository.get_logical_dimension(oid, dimension_id)
        if dimension is None:
            raise SemanticNotFoundError("SEMANTIC_LOGICAL_DIMENSION_NOT_FOUND")
        return dimension

    def _require_capability(self, oid: int, capability_id: int) -> MetricDimensionCapability:
        capability = self._repository.get_metric_dimension_capability(oid, capability_id)
        if capability is None:
            raise SemanticNotFoundError("SEMANTIC_METRIC_DIMENSION_CAPABILITY_NOT_FOUND")
        return capability

    def _require_hierarchy(self, oid: int, hierarchy_id: int) -> DimensionHierarchy:
        hierarchy = self._repository.get_dimension_hierarchy(oid, hierarchy_id)
        if hierarchy is None:
            raise SemanticNotFoundError("SEMANTIC_DIMENSION_HIERARCHY_NOT_FOUND")
        return hierarchy

    def _require_relationship(self, oid: int, relationship_id: int) -> MetricRelationship:
        relationship = self._repository.get_metric_relationship(oid, relationship_id)
        if relationship is None:
            raise SemanticNotFoundError("SEMANTIC_METRIC_RELATIONSHIP_NOT_FOUND")
        return relationship

    def _ensure_domain(self, oid: int, domain_id: int) -> None:
        if not self._domain_reader.is_active(oid, domain_id):
            raise SemanticNotFoundError("SEMANTIC_DOMAIN_NOT_FOUND")

    def _validate_logical_dimension(
        self,
        oid: int,
        payload: LogicalDimensionPayload,
    ) -> None:
        self._ensure_domain(oid, payload.domain_id)
        if payload.entity_id is None:
            return
        entity = self._require_entity(oid, payload.entity_id)
        if entity.domain_id != payload.domain_id:
            raise SemanticValidationError("SEMANTIC_CONTRACT_DOMAIN_MISMATCH")
        if (
            entity.value_domain_key
            and payload.value_domain_key != entity.value_domain_key
        ):
            raise SemanticValidationError("SEMANTIC_VALUE_DOMAIN_MISMATCH")

    def _validate_capability(
        self,
        oid: int,
        payload: MetricDimensionCapabilityPayload,
    ) -> None:
        if payload.physical_dimension_id is None:
            raise SemanticValidationError(
                "SEMANTIC_CAPABILITY_PHYSICAL_DIMENSION_REQUIRED"
            )
        facts = self._repository.capability_reference_facts(
            oid,
            payload.metric_id,
            payload.logical_dimension_id,
            payload.target_model_id,
            payload.physical_dimension_id,
            payload.relation_path,
        )
        self._ensure_capability_references(facts, payload)

    def _validate_hierarchy(self, oid: int, payload: DimensionHierarchyPayload) -> None:
        self._ensure_domain(oid, payload.domain_id)
        for level in payload.levels:
            dimension = self._repository.get_logical_dimension(oid, level.logical_dimension_id)
            if dimension is None:
                raise SemanticNotFoundError("SEMANTIC_LOGICAL_DIMENSION_NOT_FOUND")
            if dimension.domain_id != payload.domain_id:
                raise SemanticValidationError("SEMANTIC_CONTRACT_DOMAIN_MISMATCH")

    def _validate_relationship(self, oid: int, payload: MetricRelationshipPayload) -> None:
        self._ensure_domain(oid, payload.domain_id)
        target = self._repository.metric_reference(oid, payload.target_metric_id)
        driver = self._repository.metric_reference(oid, payload.driver_metric_id)
        if target is None or driver is None:
            raise SemanticNotFoundError("SEMANTIC_METRIC_NOT_FOUND")
        if target[1] != payload.domain_id or driver[1] != payload.domain_id:
            raise SemanticValidationError("SEMANTIC_CONTRACT_DOMAIN_MISMATCH")
        if target[0] != driver[0]:
            if payload.relationship_type not in {
                "CERTIFIED_DRIVER",
                "GOVERNED_ANALYSIS_RELATION",
            }:
                raise SemanticValidationError(
                    "SEMANTIC_METRIC_RELATIONSHIP_CROSS_MODEL_FORBIDDEN"
                )
            if not payload.relation_path:
                raise SemanticValidationError("SEMANTIC_RELATION_PATH_REQUIRED")
            facts = self._repository.capability_reference_facts(
                oid,
                payload.target_metric_id,
                payload.logical_dimension_ids[0] if payload.logical_dimension_ids else 0,
                target[0],
                None,
                payload.relation_path,
            )
            if len(facts.relations) != len(payload.relation_path):
                raise SemanticNotFoundError("SEMANTIC_MODEL_RELATION_NOT_FOUND")
            current_model_id = target[0]
            for relation in facts.relations:
                if relation.left_model_id == current_model_id:
                    current_model_id = relation.right_model_id
                elif relation.right_model_id == current_model_id:
                    current_model_id = relation.left_model_id
                else:
                    raise SemanticValidationError("SEMANTIC_RELATION_PATH_INVALID")
            if current_model_id != driver[0]:
                raise SemanticValidationError("SEMANTIC_RELATION_PATH_INVALID")
        if not self._repository.relation_ids_exist(oid, payload.relation_path):
            raise SemanticNotFoundError("SEMANTIC_MODEL_RELATION_NOT_FOUND")
        for logical_id in payload.logical_dimension_ids:
            dimension = self._repository.get_logical_dimension(oid, logical_id)
            if dimension is None:
                raise SemanticNotFoundError("SEMANTIC_LOGICAL_DIMENSION_NOT_FOUND")
            if dimension.domain_id != payload.domain_id:
                raise SemanticValidationError("SEMANTIC_CONTRACT_DOMAIN_MISMATCH")

    def _hierarchy_dto(
        self,
        hierarchy: DimensionHierarchy,
        levels: list[DimensionHierarchyLevel],
    ) -> DimensionHierarchyDTO:
        return DimensionHierarchyDTO(
            id=hierarchy.id or 0,
            oid=hierarchy.oid,
            domain_id=hierarchy.domain_id,
            name=hierarchy.name,
            biz_name=hierarchy.biz_name,
            description=hierarchy.description,
            hierarchy_type=hierarchy.hierarchy_type,
            contract_status=hierarchy.contract_status,
            version=hierarchy.version,
            status=hierarchy.status,
            levels=[
                DimensionHierarchyLevelDTO(
                    id=item.id or 0,
                    logical_dimension_id=item.logical_dimension_id,
                    level_order=item.level_order,
                )
                for item in sorted(levels, key=lambda value: value.level_order)
            ],
        )

    def _relationship_dto(
        self,
        relationship: MetricRelationship,
        dimensions: list[MetricRelationshipDimension],
    ) -> MetricRelationshipDTO:
        return MetricRelationshipDTO(
            id=relationship.id or 0,
            oid=relationship.oid,
            domain_id=relationship.domain_id,
            target_metric_id=relationship.target_metric_id,
            driver_metric_id=relationship.driver_metric_id,
            relationship_type=relationship.relationship_type,
            validation_method=relationship.validation_method,
            expected_direction=relationship.expected_direction,
            supported_time_roles=relationship.supported_time_roles or [],
            logical_dimension_ids=[item.logical_dimension_id for item in dimensions],
            relation_path=relationship.relation_path or [],
            contract_status=relationship.contract_status,
            version=relationship.version,
            status=relationship.status,
        )

    def _ensure_capability_references(
        self,
        facts: CapabilityReferenceFacts,
        payload: MetricDimensionCapabilityPayload,
    ) -> None:
        if facts.metric_model_id is None:
            raise SemanticNotFoundError("SEMANTIC_METRIC_NOT_FOUND")
        if facts.logical_dimension_domain_id is None:
            raise SemanticNotFoundError("SEMANTIC_LOGICAL_DIMENSION_NOT_FOUND")
        if facts.target_model_domain_id is None:
            raise SemanticNotFoundError("SEMANTIC_MODEL_NOT_FOUND")
        if facts.physical_dimension_model_id is None:
            raise SemanticNotFoundError("SEMANTIC_DIMENSION_NOT_FOUND")
        if facts.physical_dimension_model_id != payload.target_model_id:
            raise SemanticValidationError(
                "SEMANTIC_CAPABILITY_PHYSICAL_MODEL_MISMATCH"
            )
        if facts.physical_dimension_logical_id != payload.logical_dimension_id:
            raise SemanticValidationError(
                "SEMANTIC_CAPABILITY_LOGICAL_DIMENSION_MISMATCH"
            )
        if payload.binding_strategy == "SAME_MODEL":
            if facts.metric_model_id != payload.target_model_id:
                raise SemanticValidationError("SEMANTIC_CAPABILITY_MODEL_MISMATCH")
            return
        if len(facts.relations) != len(payload.relation_path):
            raise SemanticNotFoundError("SEMANTIC_MODEL_RELATION_NOT_FOUND")
        relation_by_id = {item.relation_id: item for item in facts.relations}
        current_model_id = facts.metric_model_id
        for relation_id in payload.relation_path:
            relation = relation_by_id[relation_id]
            if relation.left_model_id == current_model_id:
                current_model_id = relation.right_model_id
            elif relation.right_model_id == current_model_id:
                current_model_id = relation.left_model_id
            else:
                raise SemanticValidationError("SEMANTIC_RELATION_PATH_INVALID")
        if current_model_id != payload.target_model_id:
            raise SemanticValidationError("SEMANTIC_RELATION_PATH_INVALID")
