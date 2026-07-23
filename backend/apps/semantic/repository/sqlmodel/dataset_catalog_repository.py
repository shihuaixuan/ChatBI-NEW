"""数据集目录只读信息的 SQLModel 仓储实现。"""

from __future__ import annotations

from sqlmodel import Session

from apps.semantic.models.dto import SemanticDatasetSummary
from apps.semantic.models.orm import SemanticDataset


class SQLModelDatasetCatalogRepository:
    """按 id 直取数据集展示信息，不做工作空间或状态过滤。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_summary(self, dataset_id: int) -> SemanticDatasetSummary | None:
        dataset = self._session.get(SemanticDataset, dataset_id)
        if not isinstance(dataset, SemanticDataset) or dataset.id is None:
            return None
        return SemanticDatasetSummary(dataset_id=dataset.id, name=dataset.name)


__all__ = ["SQLModelDatasetCatalogRepository"]
