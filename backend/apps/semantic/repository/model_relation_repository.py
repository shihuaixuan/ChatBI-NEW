from typing import Protocol

from apps.semantic.models.orm import SemanticModelRelation


class ModelRelationRepository(Protocol):
    """模型关系应用服务依赖的持久化端口。"""

    def list_active(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticModelRelation]: ...

    def get_active(
        self,
        oid: int,
        relation_id: int,
    ) -> SemanticModelRelation | None: ...

    def create(self, relation: SemanticModelRelation) -> SemanticModelRelation: ...

    def update(self, relation: SemanticModelRelation) -> SemanticModelRelation: ...

    def delete(self, relation: SemanticModelRelation) -> None: ...
