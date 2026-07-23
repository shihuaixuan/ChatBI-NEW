"""数据集目录只读服务：向其他领域提供按 id 的数据集展示信息。

与执行绑定（SemanticDatasetBindingService）不同，本服务只回答"数据集是否存在、
名称是什么"，不做工作空间与状态过滤——供旧会话历史展示按 id 直取名称使用。
"""

from __future__ import annotations

from typing import Protocol

from apps.semantic.models.dto import SemanticDatasetSummary


class DatasetSummaryReader(Protocol):
    def get_summary(self, dataset_id: int) -> SemanticDatasetSummary | None: ...


class SemanticDatasetCatalogService:
    """按数据集 id 提供最小展示信息（名称与存在性）。"""

    def __init__(self, reader: DatasetSummaryReader) -> None:
        self._reader = reader

    def get_summary(self, dataset_id: int) -> SemanticDatasetSummary | None:
        return self._reader.get_summary(dataset_id)


__all__ = ["SemanticDatasetCatalogService"]
