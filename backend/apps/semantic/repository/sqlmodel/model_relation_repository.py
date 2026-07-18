from sqlmodel import Session, col, select

from apps.semantic.models.orm import SemanticModelRelation
from apps.semantic.repository.model_relation_repository import (
    ModelRelationRepository,
)
from apps.semantic.repository.sqlmodel.results import all_results


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
        self._session.commit()
        self._session.refresh(relation)
        return relation

    def delete(self, relation: SemanticModelRelation) -> None:
        self._session.delete(relation)
        self._session.commit()
