"""数据集目录只读服务：向其他领域提供按 id 的数据集展示信息。

与执行绑定（SemanticDatasetBindingService）不同，本服务只回答"数据集是否存在、
名称是什么"，不做工作空间与状态过滤——供旧会话历史展示按 id 直取名称使用。
"""

from __future__ import annotations

from typing import Protocol

from apps.semantic.models.dto import DatasetCalendarContract, SemanticDatasetSummary


class DatasetSummaryReader(Protocol):
    def get_summary(self, dataset_id: int) -> SemanticDatasetSummary | None: ...

    def get_calendar(
        self,
        workspace_id: int,
        dataset_id: int,
    ) -> DatasetCalendarContract | None: ...

    def resolve_dataset_id(
        self,
        workspace_id: int,
        dataset_or_datasource_id: int,
    ) -> int | None: ...


class SemanticDatasetCatalogService:
    """提供数据集展示信息和旧数据源引用解析能力。"""

    def __init__(self, reader: DatasetSummaryReader) -> None:
        self._reader = reader

    def get_summary(self, dataset_id: int) -> SemanticDatasetSummary | None:
        return self._reader.get_summary(dataset_id)

    def get_calendar(
        self,
        workspace_id: int,
        dataset_id: int,
    ) -> DatasetCalendarContract | None:
        """读取当前工作空间内启用数据集的业务日历契约。"""

        return self._reader.get_calendar(workspace_id, dataset_id)

    def resolve_dataset_id(
        self,
        workspace_id: int,
        dataset_or_datasource_id: int,
    ) -> int:
        """优先识别数据集 id，否则按默认模型把旧数据源 id 映射为数据集 id。"""

        resolved = self._reader.resolve_dataset_id(
            workspace_id,
            dataset_or_datasource_id,
        )
        return resolved if resolved is not None else dataset_or_datasource_id


__all__ = ["SemanticDatasetCatalogService"]
