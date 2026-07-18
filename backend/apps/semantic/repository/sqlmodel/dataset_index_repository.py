from sqlmodel import Session

from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import DatasetIndexVersion
from apps.semantic.models.orm import SemanticDataset
from apps.semantic.repository.dataset_index_repository import (
    DatasetIndexRepository,
)


class SqlModelDatasetIndexRepository(DatasetIndexRepository):
    """通过 SQLModel 暂存并提交数据集索引版本。"""

    def __init__(self, session: Session):
        self._session = session

    def stage_rebuild(self, oid: int, dataset_id: int) -> DatasetIndexVersion:
        dataset = self._session.get(SemanticDataset, dataset_id)
        if dataset is None or dataset.oid != oid or dataset.status != 1:
            raise SemanticNotFoundError("SEMANTIC_DATASET_NOT_FOUND")

        dataset.index_version = (dataset.index_version or 0) + 1
        self._session.add(dataset)
        return DatasetIndexVersion(
            dataset_id=dataset_id,
            schema_version=dataset.schema_version,
            index_version=dataset.index_version,
        )

    def commit_rebuild(self) -> None:
        self._session.commit()
