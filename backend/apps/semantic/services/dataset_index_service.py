from __future__ import annotations

from apps.semantic.repository.dataset_index_repository import (
    DatasetIndexRebuildResult,
    DatasetIndexRepository,
)


class SemanticDatasetIndexService:
    """数据集统一检索索引应用服务。"""

    def __init__(self, repository: DatasetIndexRepository):
        self._repository = repository

    def rebuild_index(
        self,
        oid: int,
        dataset_id: int,
    ) -> DatasetIndexRebuildResult:
        return self._repository.rebuild(oid, dataset_id)
