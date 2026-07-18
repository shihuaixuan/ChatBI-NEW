from __future__ import annotations

from apps.semantic.errors import (
    SemanticNotFoundError,
    SemanticValidationError,
)
from apps.semantic.models.dto import ModelRelationPayload
from apps.semantic.models.orm import SemanticModelRelation
from apps.semantic.repository.model_relation_repository import (
    ModelRelationRepository,
)
from apps.semantic.repository.model_repository import ModelReader
from apps.semantic.services.rules.model_relation import (
    RelationDomainMismatchError,
    RelationModelUnavailableError,
    validate_model_relation,
)
from apps.semantic.utils.model_update import assign_values


class SemanticModelRelationService:
    """模型关系生命周期的应用服务。"""

    def __init__(
        self,
        repository: ModelRelationRepository,
        model_reader: ModelReader,
    ):
        self._repository = repository
        self._model_reader = model_reader

    def list_model_relations(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticModelRelation]:
        return self._repository.list_active(oid, domain_id)

    def create_model_relation(
        self,
        oid: int,
        payload: ModelRelationPayload,
    ) -> SemanticModelRelation:
        self._validate_models(oid, payload)
        relation = SemanticModelRelation(**payload.model_dump(), oid=oid)
        return self._repository.create(relation)

    def update_model_relation(
        self,
        oid: int,
        relation_id: int,
        payload: ModelRelationPayload,
    ) -> SemanticModelRelation:
        relation = self._repository.get_active(oid, relation_id)
        if relation is None:
            raise SemanticNotFoundError("SEMANTIC_MODEL_RELATION_NOT_FOUND")
        self._validate_models(oid, payload)
        assign_values(relation, payload.model_dump())
        return self._repository.update(relation)

    def delete_model_relation(
        self,
        oid: int,
        relation_id: int,
    ) -> dict[str, int | bool]:
        relation = self._repository.get_active(oid, relation_id)
        if relation is None:
            raise SemanticNotFoundError("SEMANTIC_MODEL_RELATION_NOT_FOUND")
        self._repository.delete(relation)
        return {"id": relation_id, "deleted": True}

    def _validate_models(
        self,
        oid: int,
        payload: ModelRelationPayload,
    ) -> None:
        left_model = self._model_reader.get_active(oid, payload.left_model_id)
        right_model = self._model_reader.get_active(oid, payload.right_model_id)
        try:
            validate_model_relation(
                oid=oid,
                domain_id=payload.domain_id,
                left_model=left_model,
                right_model=right_model,
            )
        except RelationModelUnavailableError as error:
            raise SemanticNotFoundError(error.detail) from error
        except RelationDomainMismatchError as error:
            raise SemanticValidationError(error.detail) from error
