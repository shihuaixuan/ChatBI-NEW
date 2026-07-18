from __future__ import annotations

from typing import Protocol

from apps.semantic.models.orm import SemanticDataset


class DatasetRepository(Protocol):
    """数据集应用服务依赖的持久化端口。"""

    def list_active(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticDataset]: ...

    def get_active(self, oid: int, dataset_id: int) -> SemanticDataset | None: ...

    def create(self, dataset: SemanticDataset) -> SemanticDataset: ...

    def update(self, dataset: SemanticDataset) -> SemanticDataset: ...

    def delete(self, dataset: SemanticDataset) -> None: ...
