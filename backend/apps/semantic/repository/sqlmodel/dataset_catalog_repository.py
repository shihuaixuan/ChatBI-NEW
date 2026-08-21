"""数据集目录只读信息的 SQLModel 仓储实现。"""

from __future__ import annotations

from typing import Literal, cast

from sqlmodel import Session, col, select

from apps.semantic.models.dto import DatasetCalendarContract, SemanticDatasetSummary
from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetModelConfig,
    SemanticModel,
)


class SQLModelDatasetCatalogRepository:
    """按 id 直取数据集展示信息，不做工作空间或状态过滤。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_summary(self, dataset_id: int) -> SemanticDatasetSummary | None:
        dataset = self._session.get(SemanticDataset, dataset_id)
        if not isinstance(dataset, SemanticDataset) or dataset.id is None:
            return None
        return SemanticDatasetSummary(dataset_id=dataset.id, name=dataset.name)

    def get_calendar(
        self,
        workspace_id: int,
        dataset_id: int,
    ) -> DatasetCalendarContract | None:
        dataset = self._session.get(SemanticDataset, dataset_id)
        if (
            not isinstance(dataset, SemanticDataset)
            or dataset.oid != workspace_id
            or dataset.status != 1
        ):
            return None
        return DatasetCalendarContract(
            default_timezone=dataset.default_timezone,
            calendar_type=cast(
                Literal["NATURAL", "FISCAL", "BUSINESS"],
                dataset.calendar_type,
            ),
            week_start_day=dataset.week_start_day,
            fiscal_year_start_month=dataset.fiscal_year_start_month,
            holiday_calendar_key=dataset.holiday_calendar_key,
        )

    def resolve_dataset_id(
        self,
        workspace_id: int,
        dataset_or_datasource_id: int,
    ) -> int | None:
        """解析可用数据集；数据源关联多个数据集时遵循默认模型与排序规则。"""

        dataset = self._session.get(SemanticDataset, dataset_or_datasource_id)
        if (
            isinstance(dataset, SemanticDataset)
            and dataset.oid == workspace_id
            and dataset.status == 1
        ):
            return dataset_or_datasource_id

        statement = (
            select(SemanticDataset.id)
            .join(
                SemanticDatasetModelConfig,
                col(SemanticDatasetModelConfig.dataset_id) == col(SemanticDataset.id),
            )
            .join(
                SemanticModel,
                col(SemanticModel.id) == col(SemanticDatasetModelConfig.model_id),
            )
            .where(
                SemanticDataset.oid == workspace_id,
                SemanticDataset.status == 1,
                SemanticDatasetModelConfig.status == 1,
                SemanticModel.status == 1,
                SemanticModel.datasource_id == dataset_or_datasource_id,
            )
            .order_by(
                col(SemanticDatasetModelConfig.is_default).desc(),
                col(SemanticDatasetModelConfig.sort_order).asc(),
                col(SemanticDataset.id).asc(),
            )
            .limit(1)
        )
        resolved = self._session.exec(statement).one_or_none()
        return int(resolved) if resolved is not None else None


__all__ = ["SQLModelDatasetCatalogRepository"]
