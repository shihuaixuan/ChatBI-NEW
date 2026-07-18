from __future__ import annotations

from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import TermPayload
from apps.semantic.models.orm import SemanticTerm
from apps.semantic.repository.domain_repository import DomainRepository
from apps.semantic.repository.term_repository import TermRepository
from apps.semantic.utils.model_update import assign_values


class SemanticTermService:
    """术语管理的应用服务。"""

    def __init__(
        self,
        repository: TermRepository,
        domain_repository: DomainRepository,
    ):
        self._repository = repository
        self._domain_repository = domain_repository

    def list_terms(
        self, oid: int, domain_id: int | None = None
    ) -> list[SemanticTerm]:
        return self._repository.list_active(oid, domain_id)

    def create_term(self, oid: int, payload: TermPayload) -> SemanticTerm:
        self._require_domain(oid, payload.domain_id)
        term = SemanticTerm(**payload.model_dump(), oid=oid)
        return self._repository.create(term)

    def update_term(
        self, oid: int, term_id: int, payload: TermPayload
    ) -> SemanticTerm:
        term = self._require_term(oid, term_id)
        self._require_domain(oid, payload.domain_id)
        assign_values(term, payload.model_dump())
        return self._repository.update(term)

    def delete_term(self, oid: int, term_id: int) -> dict[str, int | bool]:
        term = self._require_term(oid, term_id)
        self._repository.delete(term)
        return {"id": term_id, "deleted": True}

    def _require_term(self, oid: int, term_id: int) -> SemanticTerm:
        term = self._repository.get_active(oid, term_id)
        if term is None:
            raise SemanticNotFoundError("SEMANTIC_TERM_NOT_FOUND")
        return term

    def _require_domain(self, oid: int, domain_id: int) -> None:
        if not self._domain_repository.is_active(oid, domain_id):
            raise SemanticNotFoundError("SEMANTIC_DOMAIN_NOT_FOUND")
