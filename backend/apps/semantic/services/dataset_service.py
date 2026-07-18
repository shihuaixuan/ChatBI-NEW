from __future__ import annotations

from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import DatasetPayload
from apps.semantic.models.orm import SemanticDataset
from apps.semantic.repository.dataset_repository import DatasetRepository
from apps.semantic.repository.domain_repository import DomainRepository
from apps.semantic.utils.model_update import assign_values


class SemanticDatasetService:
    """数据集管理的应用服务。"""

    def __init__(
        self,
        repository: DatasetRepository,
        domain_reader: DomainRepository,
    ):
        self._repository = repository
        self._domain_reader = domain_reader

    def list_datasets(
        self, oid: int, domain_id: int | None = None
    ) -> list[SemanticDataset]:
        return self._repository.list_active(oid, domain_id)

    def create_dataset(self, oid: int, payload: DatasetPayload) -> SemanticDataset:
        if not self._domain_reader.is_active(oid, payload.domain_id):
            raise SemanticNotFoundError("SEMANTIC_DOMAIN_NOT_FOUND")
        dataset = SemanticDataset(**payload.model_dump(), oid=oid)
        return self._repository.create(dataset)

    def update_dataset(
        self, oid: int, dataset_id: int, payload: DatasetPayload
    ) -> SemanticDataset:
        dataset = self._repository.get_active(oid, dataset_id)
        if dataset is None:
            raise SemanticNotFoundError("SEMANTIC_DATASET_NOT_FOUND")
        if not self._domain_reader.is_active(oid, payload.domain_id):
            raise SemanticNotFoundError("SEMANTIC_DOMAIN_NOT_FOUND")
        assign_values(dataset, payload.model_dump())
        dataset.schema_version = (dataset.schema_version or 1) + 1
        return self._repository.update(dataset)

    def delete_dataset(self, oid: int, dataset_id: int) -> dict[str, int | bool]:
        dataset = self._repository.get_active(oid, dataset_id)
        if dataset is None:
            raise SemanticNotFoundError("SEMANTIC_DATASET_NOT_FOUND")
        self._repository.delete(dataset)
        return {"id": dataset_id, "deleted": True}
