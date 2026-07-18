from collections.abc import Sequence
from typing import Protocol

from apps.semantic.models.orm import SemanticDimension, SemanticModel


class DimensionRepository(Protocol):
    """维度生命周期应用服务依赖的持久化端口。"""

    def list_active(
        self,
        oid: int,
        model_id: int | None = None,
    ) -> list[SemanticDimension]: ...

    def get_active(
        self,
        oid: int,
        dimension_id: int,
    ) -> SemanticDimension | None: ...

    def create(
        self,
        dimension: SemanticDimension,
        model: SemanticModel,
    ) -> SemanticDimension: ...

    def update(
        self,
        dimension: SemanticDimension,
        affected_models: Sequence[SemanticModel],
    ) -> SemanticDimension: ...

    def delete(
        self,
        dimension: SemanticDimension,
        model: SemanticModel | None,
    ) -> None: ...
