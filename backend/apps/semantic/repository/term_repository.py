from typing import Protocol

from apps.semantic.models.orm import SemanticTerm


class TermRepository(Protocol):
    """术语生命周期和关系同步的仓储端口。"""

    def list_active(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticTerm]: ...

    def get_active(self, oid: int, term_id: int) -> SemanticTerm | None: ...

    def create(self, term: SemanticTerm) -> SemanticTerm: ...

    def update(self, term: SemanticTerm) -> SemanticTerm: ...

    def delete(self, term: SemanticTerm) -> None: ...
