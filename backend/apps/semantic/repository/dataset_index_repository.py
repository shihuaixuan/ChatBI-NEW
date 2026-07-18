from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DatasetIndexRebuildResult:
    dataset_id: int
    index_version: int
    source_id: int
    generation: str
    job_ids: tuple[int, ...]


class DatasetIndexRepository(Protocol):
    """数据集检索索引重建的仓储端口。"""

    def rebuild(self, oid: int, dataset_id: int) -> DatasetIndexRebuildResult: ...
