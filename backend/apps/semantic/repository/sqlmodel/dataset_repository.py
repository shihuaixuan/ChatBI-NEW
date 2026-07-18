from __future__ import annotations

from sqlmodel import Session, col, select

from apps.semantic.models.orm import SemanticDataset
from apps.semantic.repository.dataset_repository import DatasetRepository
from apps.semantic.repository.sqlmodel.results import all_results
from apps.semantic.repository.sqlmodel.storage_sync import sync_dataset_assets


class SqlModelDatasetRepository(DatasetRepository):
    """基于 SQLModel 的数据集仓储实现。"""

    def __init__(self, session: Session):
        self._session = session

    def list_active(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticDataset]:
        statement = select(SemanticDataset).where(
            SemanticDataset.oid == oid,
            SemanticDataset.status == 1,
        )
        if domain_id is not None:
            statement = statement.where(SemanticDataset.domain_id == domain_id)
        return all_results(
            self._session.exec(statement.order_by(col(SemanticDataset.id)))
        )

    def get_active(self, oid: int, dataset_id: int) -> SemanticDataset | None:
        dataset = self._session.get(SemanticDataset, dataset_id)
        if dataset is None or dataset.oid != oid or dataset.status != 1:
            return None
        return dataset

    def create(self, dataset: SemanticDataset) -> SemanticDataset:
        self._session.add(dataset)
        self._session.flush()
        self._session.refresh(dataset)
        sync_dataset_assets(self._session, dataset)
        self._session.commit()
        self._session.refresh(dataset)
        return dataset

    def update(self, dataset: SemanticDataset) -> SemanticDataset:
        self._session.add(dataset)
        sync_dataset_assets(self._session, dataset)
        self._session.commit()
        self._session.refresh(dataset)
        return dataset

    def delete(self, dataset: SemanticDataset) -> None:
        self._session.delete(dataset)
        self._session.commit()
