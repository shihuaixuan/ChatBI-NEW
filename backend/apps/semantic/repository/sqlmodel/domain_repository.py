from sqlalchemy import delete
from sqlmodel import Session, col, select

from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDimension,
    SemanticDomain,
    SemanticMetric,
    SemanticModel,
    SemanticTerm,
)
from apps.semantic.repository.domain_repository import DomainRepository
from apps.semantic.repository.sqlmodel.results import all_results


class SqlModelDomainRepository(DomainRepository):
    """基于 SQLModel 的主题域仓储实现。"""

    def __init__(self, session: Session):
        self._session = session

    def list_active(self, oid: int) -> list[SemanticDomain]:
        return all_results(
            self._session.exec(
                select(SemanticDomain)
                .where(SemanticDomain.oid == oid, SemanticDomain.status == 1)
                .order_by(col(SemanticDomain.id))
            )
        )

    def get_active(self, oid: int, domain_id: int) -> SemanticDomain | None:
        domain = self._session.get(SemanticDomain, domain_id)
        if domain is None or domain.oid != oid or domain.status != 1:
            return None
        return domain

    def is_active(self, oid: int, domain_id: int) -> bool:
        return self.get_active(oid, domain_id) is not None

    def create(self, domain: SemanticDomain) -> SemanticDomain:
        self._session.add(domain)
        self._session.commit()
        self._session.refresh(domain)
        return domain

    def update(self, domain: SemanticDomain) -> SemanticDomain:
        self._session.add(domain)
        self._session.commit()
        self._session.refresh(domain)
        return domain

    def delete(self, domain: SemanticDomain) -> None:
        model_ids = all_results(
            self._session.exec(
                select(SemanticModel.id).where(
                    SemanticModel.oid == domain.oid,
                    SemanticModel.domain_id == domain.id,
                )
            )
        )
        if model_ids:
            self._session.exec(
                delete(SemanticMetric).where(
                    col(SemanticMetric.oid) == domain.oid,
                    col(SemanticMetric.model_id).in_(model_ids),
                )
            )
            self._session.exec(
                delete(SemanticDimension).where(
                    col(SemanticDimension.oid) == domain.oid,
                    col(SemanticDimension.model_id).in_(model_ids),
                )
            )
        self._session.exec(
            delete(SemanticDataset).where(
                col(SemanticDataset.oid) == domain.oid,
                col(SemanticDataset.domain_id) == domain.id,
            )
        )
        self._session.exec(
            delete(SemanticTerm).where(
                col(SemanticTerm.oid) == domain.oid,
                col(SemanticTerm.domain_id) == domain.id,
            )
        )
        self._session.exec(
            delete(SemanticModel).where(
                col(SemanticModel.oid) == domain.oid,
                col(SemanticModel.domain_id) == domain.id,
            )
        )
        self._session.delete(domain)
        self._session.commit()
