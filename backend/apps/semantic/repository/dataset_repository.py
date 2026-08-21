from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from apps.semantic.models.dto import (
    DatasetAssetPayload,
    DatasetModelConfigPayload,
    DatasetResponse,
)
from apps.semantic.models.orm import SemanticDataset


@dataclass(frozen=True)
class DatasetAssetReferenceFacts:
    """仓储查询到的正式资产引用事实，不包含业务判定。"""

    # 模型值为 (domain_id, status)，指标和维度值为 (model_id, status)。
    models: dict[int, tuple[int, int]]
    metrics: dict[int, tuple[int, int]]
    dimensions: dict[int, tuple[int, int]]
    # 层级和指标关系值为 (domain_id, status)。
    hierarchies: dict[int, tuple[int, int]]
    relationships: dict[int, tuple[int, int]]


class DatasetRepository(Protocol):
    """数据集应用服务依赖的持久化端口。"""

    def list_active(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticDataset]: ...

    def list_active_with_assets(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[DatasetResponse]: ...

    def get_active(self, oid: int, dataset_id: int) -> SemanticDataset | None: ...

    def get_asset_reference_facts(
        self,
        oid: int,
        model_ids: Sequence[int],
        metric_ids: Sequence[int],
        dimension_ids: Sequence[int],
        hierarchy_ids: Sequence[int],
        relationship_ids: Sequence[int],
    ) -> DatasetAssetReferenceFacts: ...

    def create(
        self,
        dataset: SemanticDataset,
        model_configs: list[DatasetModelConfigPayload],
        assets: list[DatasetAssetPayload],
    ) -> SemanticDataset: ...

    def update(
        self,
        dataset: SemanticDataset,
        model_configs: list[DatasetModelConfigPayload],
        assets: list[DatasetAssetPayload],
    ) -> SemanticDataset: ...

    def delete(self, dataset: SemanticDataset) -> None: ...
