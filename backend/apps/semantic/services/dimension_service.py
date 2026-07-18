from __future__ import annotations

from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import DimensionPayload
from apps.semantic.models.orm import SemanticDimension, SemanticModel
from apps.semantic.repository.dimension_repository import DimensionRepository
from apps.semantic.repository.model_repository import ModelReader
from apps.semantic.utils.model_update import assign_values


class SemanticDimensionService:
    """维度及维度值生命周期的应用服务。"""

    def __init__(
        self,
        repository: DimensionRepository,
        model_reader: ModelReader,
    ):
        self._repository = repository
        self._model_reader = model_reader

    def list_dimensions(
        self,
        oid: int,
        model_id: int | None = None,
    ) -> list[SemanticDimension]:
        return self._repository.list_active(oid, model_id)

    def create_dimension(
        self,
        oid: int,
        payload: DimensionPayload,
    ) -> SemanticDimension:
        model = self._require_model(oid, payload.model_id)
        dimension = SemanticDimension(**payload.model_dump(), oid=oid)
        return self._repository.create(dimension, model)

    def update_dimension(
        self,
        oid: int,
        dimension_id: int,
        payload: DimensionPayload,
    ) -> SemanticDimension:
        dimension = self._repository.get_active(oid, dimension_id)
        if dimension is None:
            raise SemanticNotFoundError("SEMANTIC_DIMENSION_NOT_FOUND")
        source_model = self._model_reader.get_active(oid, dimension.model_id)
        target_model = self._require_model(oid, payload.model_id)
        assign_values(dimension, payload.model_dump())
        return self._repository.update(
            dimension,
            [model for model in (source_model, target_model) if model is not None],
        )

    def delete_dimension(
        self,
        oid: int,
        dimension_id: int,
    ) -> dict[str, int | bool]:
        dimension = self._repository.get_active(oid, dimension_id)
        if dimension is None:
            raise SemanticNotFoundError("SEMANTIC_DIMENSION_NOT_FOUND")
        model = self._model_reader.get_active(oid, dimension.model_id)
        self._repository.delete(dimension, model)
        return {"id": dimension_id, "deleted": True}

    def _require_model(self, oid: int, model_id: int) -> SemanticModel:
        model = self._model_reader.get_active(oid, model_id)
        if model is None:
            raise SemanticNotFoundError("SEMANTIC_MODEL_NOT_FOUND")
        return model
