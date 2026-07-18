from sqlmodel import Session

from apps.retrieval.semantic_indexing import SemanticIndexCoordinator
from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.orm import SemanticDataset
from apps.semantic.repository.dataset_index_repository import (
    DatasetIndexRebuildResult,
    DatasetIndexRepository,
)


class SqlModelDatasetIndexRepository(DatasetIndexRepository):
    """通过 SQLModel 事务更新索引版本并创建检索任务。"""

    def __init__(self, session: Session):
        self._session = session

    def rebuild(self, oid: int, dataset_id: int) -> DatasetIndexRebuildResult:
        dataset = self._session.get(SemanticDataset, dataset_id)
        if dataset is None or dataset.oid != oid or dataset.status != 1:
            raise SemanticNotFoundError("SEMANTIC_DATASET_NOT_FOUND")

        dataset.index_version = (dataset.index_version or 0) + 1
        index_enqueue = SemanticIndexCoordinator(
            self._session
        ).enqueue_dataset_rebuild(
            tenant_id=oid,
            dataset=dataset,
        )
        self._session.add(dataset)
        self._session.commit()
        return DatasetIndexRebuildResult(
            dataset_id=dataset_id,
            index_version=dataset.index_version,
            source_id=index_enqueue.source_id,
            generation=index_enqueue.generation.generation,
            job_ids=tuple(index_enqueue.generation.job_ids),
        )
