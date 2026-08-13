"""完整语义契约的基础管理服务。"""

from apps.semantic.errors import SemanticNotFoundError, SemanticValidationError
from apps.semantic.models.dto import (
    BusinessEntityPayload,
    LogicalDimensionPayload,
    MetricDimensionCapabilityPayload,
)
from apps.semantic.models.orm import (
    BusinessEntity,
    LogicalDimension,
    MetricDimensionCapability,
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
        return self._repository.update_metric_dimension_capability(capability)

    def delete_metric_dimension_capability(
        self,
        oid: int,
        capability_id: int,
    ) -> dict[str, int | bool]:
        capability = self._require_capability(oid, capability_id)
        self._repository.delete_metric_dimension_capability(capability)
        return {"id": capability_id, "deleted": True}

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
