from sqlmodel import Session, col, select

from apps.semantic.models.orm import (
    MetricDimensionCapability,
    MetricRelationship,
    SemanticModelRelation,
)
from apps.semantic.repository.model_relation_repository import (
    ModelRelationRepository,
)
from apps.semantic.repository.sqlmodel.results import all_results
from apps.semantic.repository.sqlmodel.storage_sync import (
    invalidate_model_relation_contract,
)


class SqlModelModelRelationRepository(ModelRelationRepository):
    """基于 SQLModel 的模型关系仓储实现。"""

    def __init__(self, session: Session):
        self._session = session

    def list_active(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticModelRelation]:
        statement = select(SemanticModelRelation).where(
            SemanticModelRelation.oid == oid,
            SemanticModelRelation.status == 1,
        )
        if domain_id is not None:
            statement = statement.where(SemanticModelRelation.domain_id == domain_id)
        return all_results(
            self._session.exec(statement.order_by(col(SemanticModelRelation.id)))
        )

    def get_active(
        self,
        oid: int,
        relation_id: int,
    ) -> SemanticModelRelation | None:
        relation = self._session.get(SemanticModelRelation, relation_id)
        if relation is None or relation.oid != oid or relation.status != 1:
            return None
        return relation

    def create(self, relation: SemanticModelRelation) -> SemanticModelRelation:
        self._session.add(relation)
        self._session.commit()
        self._session.refresh(relation)
        return relation

    def update(self, relation: SemanticModelRelation) -> SemanticModelRelation:
        self._session.add(relation)
        invalidate_model_relation_contract(self._session, relation)
        self._session.commit()
        self._session.refresh(relation)
        return relation

    def delete(self, relation: SemanticModelRelation) -> None:
        invalidate_model_relation_contract(self._session, relation)
        self._session.delete(relation)
        self._session.commit()

    def model_relation_is_referenced(self, oid: int, relation_id: int) -> bool:
        """检查能力契约和指标关系的关系路径引用。"""

        capabilities = all_results(
            self._session.exec(
                select(MetricDimensionCapability).where(
                    MetricDimensionCapability.oid == oid,
                    MetricDimensionCapability.status == 1,
                )
            )
        )
        if any(relation_id in (item.relation_path or []) for item in capabilities):
            return True
        relationships = all_results(
            self._session.exec(
                select(MetricRelationship).where(
                    MetricRelationship.oid == oid,
                    MetricRelationship.status == 1,
                )
            )
        )
        return any(relation_id in (item.relation_path or []) for item in relationships)
