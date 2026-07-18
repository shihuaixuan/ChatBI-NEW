from typing import Protocol

from apps.semantic.models.dto import (
    DatasetIndexEnqueueResult,
    DatasetIndexRebuildResult,
    DatasetIndexVersion,
    DatasetSchema,
)


class DatasetSchemaReader(Protocol):
    """数据集 Schema 的只读公开能力。"""

    def build_dataset_schema(self, oid: int, dataset_id: int) -> DatasetSchema: ...


class DatasetIndexGateway(Protocol):
    """Semantic 调用统一检索索引的跨领域端口。"""

    def enqueue_dataset_rebuild(
        self,
        *,
        tenant_id: int,
        version: DatasetIndexVersion,
        schema: DatasetSchema,
    ) -> DatasetIndexEnqueueResult: ...


class DatasetIndexRepository(Protocol):
    """数据集索引版本和事务提交的仓储端口。"""

    def stage_rebuild(self, oid: int, dataset_id: int) -> DatasetIndexVersion: ...

    def commit_rebuild(self) -> None: ...


__all__ = [
    "DatasetIndexGateway",
    "DatasetIndexRepository",
    "DatasetIndexRebuildResult",
    "DatasetSchemaReader",
]
