from sqlmodel import Session, col, select

from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticTerm,
)
from apps.semantic.repository.sqlmodel.results import all_results
from apps.semantic.repository.sqlmodel.storage_sync import (
    delete_term_relations,
    sync_term_relations,
)
from apps.semantic.repository.term_repository import (
    TermReferenceValidation,
    TermRepository,
)


class SqlModelTermRepository(TermRepository):
    """基于 SQLModel 的术语仓储实现。"""

    def __init__(self, session: Session):
        self._session = session

    def list_all(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticTerm]:
        conditions = [SemanticTerm.oid == oid]
        if domain_id is not None:
            conditions.append(SemanticTerm.domain_id == domain_id)
        return all_results(
            self._session.exec(
                select(SemanticTerm)
                .where(*conditions)
                .order_by(col(SemanticTerm.id))
            )
        )

    def get(self, oid: int, term_id: int) -> SemanticTerm | None:
        term = self._session.get(SemanticTerm, term_id)
        if term is None or term.oid != oid:
            return None
        return term

    def name_exists(
        self,
        oid: int,
        domain_id: int,
        name: str,
        exclude_id: int | None = None,
    ) -> bool:
        statement = select(SemanticTerm.id).where(
            SemanticTerm.oid == oid,
            SemanticTerm.domain_id == domain_id,
            SemanticTerm.name == name,
        )
        if exclude_id is not None:
            statement = statement.where(SemanticTerm.id != exclude_id)
        return bool(all_results(self._session.exec(statement)))

    def validate_references(
        self,
        oid: int,
        domain_id: int,
        dataset_ids: list[int],
        metric_ids: list[int],
        dimension_ids: list[int],
    ) -> TermReferenceValidation:
        valid_dataset_ids = self._valid_dataset_ids(oid, domain_id, dataset_ids)
        valid_metric_ids = self._valid_metric_ids(oid, domain_id, metric_ids)
        valid_dimension_ids = self._valid_dimension_ids(
            oid, domain_id, dimension_ids
        )
        return TermReferenceValidation(
            invalid_dataset_ids=tuple(
                value for value in dataset_ids if value not in valid_dataset_ids
            ),
            invalid_metric_ids=tuple(
                value for value in metric_ids if value not in valid_metric_ids
            ),
            invalid_dimension_ids=tuple(
                value for value in dimension_ids if value not in valid_dimension_ids
            ),
        )

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
        delete_term_relations(self._session, term)
        self._session.delete(term)
        self._session.commit()

    def delete_many(self, terms: list[SemanticTerm]) -> None:
        for term in terms:
            delete_term_relations(self._session, term)
            self._session.delete(term)
        self._session.commit()

    def _valid_dataset_ids(
        self,
        oid: int,
        domain_id: int,
        dataset_ids: list[int],
    ) -> set[int]:
        if not dataset_ids:
            return set()
        return set(
            all_results(
                self._session.exec(
                    select(SemanticDataset.id).where(
                        SemanticDataset.oid == oid,
                        SemanticDataset.domain_id == domain_id,
                        SemanticDataset.status == 1,
                        col(SemanticDataset.id).in_(dataset_ids),
                    )
                )
            )
        )

    def _valid_metric_ids(
        self,
        oid: int,
        domain_id: int,
        metric_ids: list[int],
    ) -> set[int]:
        if not metric_ids:
            return set()
        return set(
            all_results(
                self._session.exec(
                    select(SemanticMetric.id)
                    .join(
                        SemanticModel,
                        col(SemanticModel.id) == col(SemanticMetric.model_id),
                    )
                    .where(
                        SemanticMetric.oid == oid,
                        SemanticMetric.status == 1,
                        SemanticModel.oid == oid,
                        SemanticModel.domain_id == domain_id,
                        SemanticModel.status == 1,
                        col(SemanticMetric.id).in_(metric_ids),
                    )
                )
            )
        )

    def _valid_dimension_ids(
        self,
        oid: int,
        domain_id: int,
        dimension_ids: list[int],
    ) -> set[int]:
        if not dimension_ids:
            return set()
        return set(
            all_results(
                self._session.exec(
                    select(SemanticDimension.id)
                    .join(
                        SemanticModel,
                        col(SemanticModel.id) == col(SemanticDimension.model_id),
                    )
                    .where(
                        SemanticDimension.oid == oid,
                        SemanticDimension.status == 1,
                        SemanticModel.oid == oid,
                        SemanticModel.domain_id == domain_id,
                        SemanticModel.status == 1,
                        col(SemanticDimension.id).in_(dimension_ids),
                    )
                )
            )
        )
