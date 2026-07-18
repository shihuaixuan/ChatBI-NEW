from dataclasses import dataclass
from typing import Protocol

from apps.semantic.models.orm import SemanticTerm


@dataclass(frozen=True, slots=True)
class TermReferenceValidation:
    """术语引用中不存在或不属于目标主题域的资源。"""

    invalid_dataset_ids: tuple[int, ...] = ()
    invalid_metric_ids: tuple[int, ...] = ()
    invalid_dimension_ids: tuple[int, ...] = ()


class TermRepository(Protocol):
    """术语生命周期和关系同步的仓储端口。"""

    def list_all(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticTerm]: ...

    def get(self, oid: int, term_id: int) -> SemanticTerm | None: ...

    def name_exists(
        self,
        oid: int,
        domain_id: int,
        name: str,
        exclude_id: int | None = None,
    ) -> bool: ...

    def validate_references(
        self,
        oid: int,
        domain_id: int,
        dataset_ids: list[int],
        metric_ids: list[int],
        dimension_ids: list[int],
    ) -> TermReferenceValidation: ...

    def create(self, term: SemanticTerm) -> SemanticTerm: ...

    def update(self, term: SemanticTerm) -> SemanticTerm: ...

    def delete(self, term: SemanticTerm) -> None: ...

    def delete_many(self, terms: list[SemanticTerm]) -> None: ...
