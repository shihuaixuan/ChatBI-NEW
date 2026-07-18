from sqlalchemy import delete
from sqlmodel import Session, col, select

from apps.semantic.models.orm import (
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticModelRelation,
)
from apps.semantic.repository.model_repository import (
    ModelRepository,
    SemanticModelAssetBundle,
)
from apps.semantic.repository.sqlmodel.results import all_results
from apps.semantic.repository.sqlmodel.storage_sync import sync_model_structure


class SqlModelModelRepository(ModelRepository):
    """基于 SQLModel 的模型仓储实现。"""

    def __init__(self, session: Session):
        self._session = session

    def list_active(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticModel]:
        statement = select(SemanticModel).where(
            SemanticModel.oid == oid,
            SemanticModel.status == 1,
        )
        if domain_id is not None:
            statement = statement.where(SemanticModel.domain_id == domain_id)
        return all_results(
            self._session.exec(statement.order_by(col(SemanticModel.id)))
        )

    def get_active(self, oid: int, model_id: int) -> SemanticModel | None:
        model = self._session.get(SemanticModel, model_id)
        if model is None or model.oid != oid or model.status != 1:
            return None
        return model

    def create(self, model: SemanticModel) -> SemanticModel:
        self._session.add(model)
        self._session.flush()
        self._session.refresh(model)
        sync_model_structure(self._session, model)
        self._session.commit()
        self._session.refresh(model)
        return model

    def update(self, model: SemanticModel) -> SemanticModel:
        self._session.add(model)
        sync_model_structure(self._session, model)
        self._session.commit()
        self._session.refresh(model)
        return model

    def delete(self, model: SemanticModel) -> None:
        if model.id is None:
            raise ValueError("SEMANTIC_MODEL_NOT_PERSISTED")
        self._session.exec(
            delete(SemanticModelRelation).where(
                col(SemanticModelRelation.oid) == model.oid,
                (col(SemanticModelRelation.left_model_id) == model.id)
                | (col(SemanticModelRelation.right_model_id) == model.id),
            )
        )
        self._session.exec(
            delete(SemanticMetric).where(
                col(SemanticMetric.oid) == model.oid,
                col(SemanticMetric.model_id) == model.id,
            )
        )
        self._session.exec(
            delete(SemanticDimension).where(
                col(SemanticDimension.oid) == model.oid,
                col(SemanticDimension.model_id) == model.id,
            )
        )
        self._session.delete(model)
        self._session.commit()

    def create_with_assets(
        self,
        bundle: SemanticModelAssetBundle,
    ) -> SemanticModelAssetBundle:
        self._session.add(bundle.model)
        self._session.flush()
        self._session.refresh(bundle.model)
        if bundle.model.id is None:
            raise ValueError("SEMANTIC_MODEL_NOT_PERSISTED")
        for dimension in bundle.dimensions:
            dimension.model_id = bundle.model.id
            self._session.add(dimension)
        for metric in bundle.metrics:
            metric.model_id = bundle.model.id
            self._session.add(metric)
        sync_model_structure(self._session, bundle.model)
        self._session.commit()
        self._session.refresh(bundle.model)
        return bundle
