"""数据集索引重建的公开数据契约。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DatasetIndexVersion:
    """Semantic 已暂存的数据集索引版本。"""

    dataset_id: int
    schema_version: int
    index_version: int


@dataclass(frozen=True, slots=True)
class DatasetIndexEnqueueResult:
    """索引端口返回的任务创建结果。"""

    source_id: int
    generation: str
    job_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DatasetIndexRebuildResult:
    """Semantic 对调用方返回的索引重建结果。"""

    dataset_id: int
    index_version: int
    source_id: int
    generation: str
    job_ids: tuple[int, ...]
