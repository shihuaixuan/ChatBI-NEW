"""数据集执行绑定公开服务：其他领域据此确定数据集的执行数据源。"""

from __future__ import annotations

from typing import Protocol

from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import SemanticDatasetExecutionBinding


class DatasetBindingRepository(Protocol):
    def resolve(
        self,
        workspace_id: int,
        dataset_id: int,
    ) -> SemanticDatasetExecutionBinding | None: ...


class SemanticDatasetBindingService:
    """解析数据集的执行绑定；数据集与模型规则是 Semantic 业务不变量。"""

    def __init__(self, repository: DatasetBindingRepository) -> None:
        self._repository = repository

    def resolve_execution_binding(
        self,
        workspace_id: int,
        dataset_id: int,
    ) -> SemanticDatasetExecutionBinding:
        try:
            binding = self._repository.resolve(workspace_id, dataset_id)
        except LookupError as exc:
            raise SemanticNotFoundError("DATASET_MODEL_NOT_CONFIGURED") from exc
        if binding is None:
            raise SemanticNotFoundError("DATASET_NOT_ACCESSIBLE")
        return binding


__all__ = ["SemanticDatasetBindingService"]
