from __future__ import annotations

from collections.abc import Sequence

from sqlmodel import Session, col, select

from apps.semantic.models.dto import (
    DatasetAssetPayload,
    DatasetAssetResponse,
    DatasetModelConfigPayload,
    DatasetModelConfigResponse,
    DatasetResponse,
)
from apps.semantic.models.orm import (
    DimensionHierarchy,
    MetricRelationship,
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
)
from apps.semantic.repository.dataset_repository import (
    DatasetAssetReferenceFacts,
    DatasetRepository,
)
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

    def list_active_with_assets(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[DatasetResponse]:
        """返回数据集及正式资产，禁止管理端再读取旧 JSON 明细。"""

        datasets = self.list_active(oid, domain_id)
        dataset_ids = [item.id for item in datasets if item.id is not None]
        if not dataset_ids:
            return []
        configs = all_results(
            self._session.exec(
                select(SemanticDatasetModelConfig)
                .where(
                    SemanticDatasetModelConfig.oid == oid,
                    col(SemanticDatasetModelConfig.dataset_id).in_(dataset_ids),
                    SemanticDatasetModelConfig.status == 1,
                )
                .order_by(
                    col(SemanticDatasetModelConfig.dataset_id),
                    col(SemanticDatasetModelConfig.sort_order),
                    col(SemanticDatasetModelConfig.id),
                )
            )
        )
        assets = all_results(
            self._session.exec(
                select(SemanticDatasetAsset)
                .where(
                    SemanticDatasetAsset.oid == oid,
                    col(SemanticDatasetAsset.dataset_id).in_(dataset_ids),
                    SemanticDatasetAsset.status == 1,
                )
                .order_by(
                    col(SemanticDatasetAsset.dataset_id),
                    col(SemanticDatasetAsset.sort_order),
                    col(SemanticDatasetAsset.id),
                )
            )
        )
        configs_by_dataset: dict[int, list[DatasetModelConfigResponse]] = {}
        for config in configs:
            configs_by_dataset.setdefault(config.dataset_id, []).append(
                DatasetModelConfigResponse.model_validate(config)
            )
        assets_by_dataset: dict[int, list[DatasetAssetResponse]] = {}
        for asset in assets:
            assets_by_dataset.setdefault(asset.dataset_id, []).append(
                DatasetAssetResponse.model_validate(asset)
            )
        result: list[DatasetResponse] = []
        for dataset in datasets:
            if dataset.id is None:
                continue
            result.append(
                DatasetResponse.model_validate(dataset).model_copy(
                    update={
                        "model_configs": configs_by_dataset.get(dataset.id, []),
                        "assets": assets_by_dataset.get(dataset.id, []),
                    }
                )
            )
        return result

    def get_asset_reference_facts(
        self,
        oid: int,
        model_ids: Sequence[int],
        metric_ids: Sequence[int],
        dimension_ids: Sequence[int],
        hierarchy_ids: Sequence[int],
        relationship_ids: Sequence[int],
    ) -> DatasetAssetReferenceFacts:
        """只查询正式引用的归属事实，业务规则由应用服务统一判定。"""

        models = all_results(
            self._session.exec(
                select(SemanticModel).where(
                    col(SemanticModel.oid) == oid,
                    col(SemanticModel.id).in_(model_ids),
                )
            )
        )
        metrics = all_results(
            self._session.exec(
                select(SemanticMetric).where(
                    col(SemanticMetric.oid) == oid,
                    col(SemanticMetric.id).in_(metric_ids or [-1]),
                )
            )
        )
        dimensions = all_results(
            self._session.exec(
                select(SemanticDimension).where(
                    col(SemanticDimension.oid) == oid,
                    col(SemanticDimension.id).in_(dimension_ids or [-1]),
                )
            )
        )
        hierarchies = all_results(
            self._session.exec(
                select(DimensionHierarchy).where(
                    col(DimensionHierarchy.oid) == oid,
                    col(DimensionHierarchy.id).in_(hierarchy_ids or [-1]),
                )
            )
        )
        relationships = all_results(
            self._session.exec(
                select(MetricRelationship).where(
                    col(MetricRelationship.oid) == oid,
                    col(MetricRelationship.id).in_(relationship_ids or [-1]),
                )
            )
        )
        return DatasetAssetReferenceFacts(
            models={
                model.id: (model.domain_id, model.status)
                for model in models
                if model.id is not None
            },
            metrics={
                metric.id: (metric.model_id, metric.status)
                for metric in metrics
                if metric.id is not None
            },
            dimensions={
                dimension.id: (dimension.model_id, dimension.status)
                for dimension in dimensions
                if dimension.id is not None
            },
            hierarchies={
                hierarchy.id: (hierarchy.domain_id, hierarchy.status)
                for hierarchy in hierarchies
                if hierarchy.id is not None
            },
            relationships={
                relationship.id: (relationship.domain_id, relationship.status)
                for relationship in relationships
                if relationship.id is not None
            },
        )

    def get_active(self, oid: int, dataset_id: int) -> SemanticDataset | None:
        dataset = self._session.get(SemanticDataset, dataset_id)
        if dataset is None or dataset.oid != oid or dataset.status != 1:
            return None
        return dataset

    def create(
        self,
        dataset: SemanticDataset,
        model_configs: list[DatasetModelConfigPayload],
        assets: list[DatasetAssetPayload],
    ) -> SemanticDataset:
        self._session.add(dataset)
        self._session.flush()
        self._session.refresh(dataset)
        sync_dataset_assets(self._session, dataset, model_configs, assets)
        self._session.commit()
        self._session.refresh(dataset)
        return dataset

    def update(
        self,
        dataset: SemanticDataset,
        model_configs: list[DatasetModelConfigPayload],
        assets: list[DatasetAssetPayload],
    ) -> SemanticDataset:
        self._session.add(dataset)
        sync_dataset_assets(self._session, dataset, model_configs, assets)
        self._session.commit()
        self._session.refresh(dataset)
        return dataset

    def delete(self, dataset: SemanticDataset) -> None:
        self._session.delete(dataset)
        self._session.commit()
