"""数据集执行绑定的 SQLModel 仓储实现。"""

from __future__ import annotations

from sqlmodel import Session, col, select

from apps.semantic.models.dto import SemanticDatasetExecutionBinding
from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetModelConfig,
    SemanticModel,
)


class SQLModelDatasetBindingRepository:
    """按默认模型与配置顺序解析数据集的执行模型与数据源。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        workspace_id: int,
        dataset_id: int,
    ) -> SemanticDatasetExecutionBinding | None:
        """数据集无效或无权限返回 None；未配置可用模型抛 LookupError。"""

        dataset = self._session.get(SemanticDataset, dataset_id)
        if (
            not isinstance(dataset, SemanticDataset)
            or dataset.id is None
            or dataset.oid != workspace_id
            or dataset.status != 1
        ):
            return None

        model = self._resolve_dataset_model(workspace_id, dataset)
        if model is None or model.datasource_id is None:
            raise LookupError("DATASET_MODEL_NOT_CONFIGURED")

        return SemanticDatasetExecutionBinding(
            dataset_id=dataset.id,
            dataset_name=dataset.name,
            datasource_id=model.datasource_id,
        )

    def _resolve_dataset_model(
        self,
        workspace_id: int,
        dataset: SemanticDataset,
    ) -> SemanticModel | None:
        if dataset.default_model_id:
            default_model = self._session.get(SemanticModel, dataset.default_model_id)
            if (
                isinstance(default_model, SemanticModel)
                and default_model.oid == workspace_id
                and default_model.domain_id == dataset.domain_id
                and default_model.status == 1
            ):
                return default_model

        # 数据集没有默认模型时，按配置顺序选择第一个可用模型作为执行入口。
        statement = (
            select(SemanticModel)
            .join(
                SemanticDatasetModelConfig,
                col(SemanticDatasetModelConfig.model_id) == col(SemanticModel.id),
            )
            .where(
                SemanticDatasetModelConfig.oid == workspace_id,
                SemanticDatasetModelConfig.dataset_id == dataset.id,
                SemanticDatasetModelConfig.status == 1,
                SemanticModel.oid == workspace_id,
                SemanticModel.domain_id == dataset.domain_id,
                SemanticModel.status == 1,
            )
            .order_by(
                col(SemanticDatasetModelConfig.is_default).desc(),
                col(SemanticDatasetModelConfig.sort_order).asc(),
                col(SemanticModel.id).asc(),
            )
            .limit(1)
        )
        model = self._session.exec(statement).first()
        return model if isinstance(model, SemanticModel) else None


__all__ = ["SQLModelDatasetBindingRepository"]
