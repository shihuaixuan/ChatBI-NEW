from collections.abc import Sequence

from sqlmodel import Session, col, select

from apps.semantic.models.orm import SemanticDimension, SemanticModel
from apps.semantic.repository.dimension_repository import DimensionRepository
from apps.semantic.repository.sqlmodel.results import all_results
from apps.semantic.repository.sqlmodel.storage_sync import (
    mark_model_schema_changed,
    sync_dimension_relations,
    sync_dimension_values,
)


class SqlModelDimensionRepository(DimensionRepository):
    """基于 SQLModel 的维度仓储实现。"""

    def __init__(self, session: Session):
        self._session = session

    def list_active(
        self,
        oid: int,
        model_id: int | None = None,
    ) -> list[SemanticDimension]:
        statement = select(SemanticDimension).where(
            SemanticDimension.oid == oid,
            SemanticDimension.status == 1,
        )
        if model_id is not None:
            statement = statement.where(SemanticDimension.model_id == model_id)
        return all_results(
            self._session.exec(statement.order_by(col(SemanticDimension.id)))
        )

    def get_active(
        self,
        oid: int,
        dimension_id: int,
    ) -> SemanticDimension | None:
        dimension = self._session.get(SemanticDimension, dimension_id)
        if dimension is None or dimension.oid != oid or dimension.status != 1:
            return None
        return dimension

    def create(
        self,
        dimension: SemanticDimension,
        model: SemanticModel,
    ) -> SemanticDimension:
        self._session.add(dimension)
        self._session.flush()
        self._session.refresh(dimension)
        sync_dimension_values(self._session, dimension)
        sync_dimension_relations(self._session, dimension)
        mark_model_schema_changed(self._session, model)
        self._session.commit()
        self._session.refresh(dimension)
        return dimension

    def update(
        self,
        dimension: SemanticDimension,
        affected_models: Sequence[SemanticModel],
    ) -> SemanticDimension:
        self._session.add(dimension)
        sync_dimension_values(self._session, dimension)
        sync_dimension_relations(self._session, dimension)
        self._mark_models(affected_models)
        self._session.commit()
        self._session.refresh(dimension)
        return dimension

    def delete(
        self,
        dimension: SemanticDimension,
        model: SemanticModel | None,
    ) -> None:
        self._session.delete(dimension)
        if model is not None:
            mark_model_schema_changed(self._session, model)
        self._session.commit()

    def _mark_models(self, models: Sequence[SemanticModel]) -> None:
        seen: set[int] = set()
        for model in models:
            key = model.id if model.id is not None else id(model)
            if key in seen:
                continue
            seen.add(key)
            mark_model_schema_changed(self._session, model)
