from sqlmodel import Session, col, select

from apps.semantic.models.orm import SemanticTerm
from apps.semantic.repository.sqlmodel.results import all_results
from apps.semantic.repository.sqlmodel.storage_sync import sync_term_relations
from apps.semantic.repository.term_repository import TermRepository


class SqlModelTermRepository(TermRepository):
    """基于 SQLModel 的术语仓储实现。"""

    def __init__(self, session: Session):
        self._session = session

    def list_active(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticTerm]:
        conditions = [SemanticTerm.oid == oid, SemanticTerm.status == 1]
        if domain_id is not None:
            conditions.append(SemanticTerm.domain_id == domain_id)
        return all_results(
            self._session.exec(
                select(SemanticTerm)
                .where(*conditions)
                .order_by(col(SemanticTerm.id))
            )
        )

    def get_active(self, oid: int, term_id: int) -> SemanticTerm | None:
        term = self._session.get(SemanticTerm, term_id)
        if term is None or term.oid != oid or term.status != 1:
            return None
        return term

    def create(self, term: SemanticTerm) -> SemanticTerm:
        self._session.add(term)
        self._session.flush()
        self._session.refresh(term)
        sync_term_relations(self._session, term)
        self._session.commit()
        self._session.refresh(term)
        return term

    def update(self, term: SemanticTerm) -> SemanticTerm:
        self._session.add(term)
        sync_term_relations(self._session, term)
        self._session.commit()
        self._session.refresh(term)
        return term

    def delete(self, term: SemanticTerm) -> None:
        self._session.delete(term)
        self._session.commit()
