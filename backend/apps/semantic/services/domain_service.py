from __future__ import annotations

from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import DomainPayload
from apps.semantic.models.orm import SemanticDomain
from apps.semantic.repository.domain_repository import DomainRepository
from apps.semantic.utils.model_update import assign_values


class SemanticDomainService:
    """主题域管理的应用服务。"""

    def __init__(self, repository: DomainRepository):
        self._repository = repository

    def list_domains(self, oid: int) -> list[SemanticDomain]:
        return self._repository.list_active(oid)

    def create_domain(self, oid: int, payload: DomainPayload) -> SemanticDomain:
        domain = SemanticDomain(**payload.model_dump(), oid=oid)
        return self._repository.create(domain)

    def update_domain(
        self, oid: int, domain_id: int, payload: DomainPayload
    ) -> SemanticDomain:
        domain = self._require_domain(oid, domain_id)
        assign_values(domain, payload.model_dump())
        return self._repository.update(domain)

    def delete_domain(self, oid: int, domain_id: int) -> dict[str, int | bool]:
        domain = self._require_domain(oid, domain_id)
        self._repository.delete(domain)
        return {"id": domain_id, "deleted": True}

    def _require_domain(self, oid: int, domain_id: int) -> SemanticDomain:
        domain = self._repository.get_active(oid, domain_id)
        if domain is None:
            raise SemanticNotFoundError("SEMANTIC_DOMAIN_NOT_FOUND")
        return domain
