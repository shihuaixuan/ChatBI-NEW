from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from apps.data_training.models.data_training_model import DataTraining
from apps.semantic.assets.models import DatasetProfileRuntime
from apps.semantic.models.semantic_model import (
    AssetStatus,
    DimensionType,
    SemanticDimension,
    SemanticMetric,
)
from apps.terminology.models.terminology_model import Terminology


class DatasetScope(BaseModel):
    oid: int = 1
    datasource_id: int | None = None
    dataset_id: int | str
    table_ids: list[int] = Field(default_factory=list)
    explicit: bool = False

    @classmethod
    def explicit_scope(
        cls,
        dataset_id: int | str,
        oid: int = 1,
        datasource_id: int | None = None,
        table_ids: list[int] | None = None,
    ) -> DatasetScope:
        return cls(
            oid=oid,
            datasource_id=datasource_id,
            dataset_id=dataset_id,
            table_ids=sorted(set(table_ids or [])),
            explicit=True,
        )

    @classmethod
    def virtual(cls, oid: int, datasource_id: int | None, table_ids: list[int] | None = None) -> DatasetScope:
        if datasource_id is None:
            raise ValueError("datasource_id is required for virtual dataset scope")
        normalized_table_ids = sorted(set(table_ids or []))
        table_text = ",".join(str(item) for item in normalized_table_ids) or "all"
        table_hash = hashlib.sha1(table_text.encode("utf-8")).hexdigest()[:10]
        return cls(
            oid=oid,
            datasource_id=datasource_id,
            table_ids=normalized_table_ids,
            dataset_id=f"virtual:{oid}:{datasource_id}:{table_hash}",
            explicit=False,
        )


class DatasetProfileService:
    def __init__(self, session: Any | None = None):
        self.session = session

    def build_profile(self, scope: DatasetScope) -> DatasetProfileRuntime:
        if self.session is None or not hasattr(self.session, "exec") or scope.datasource_id is None:
            return DatasetProfileRuntime(dataset_id=scope.dataset_id)

        metrics = self._load_metrics(scope)
        dimensions = self._load_dimensions(scope)
        terms = self._load_terms(scope)
        examples = self._load_examples(scope)

        core_metric_ids = [metric.id for metric in metrics if metric.id is not None and getattr(metric, "is_core", False)]
        if not core_metric_ids:
            core_metric_ids = [metric.id for metric in metrics[:8] if metric.id is not None]

        default_time_dimension = next(
            (
                dimension.id
                for dimension in dimensions
                if dimension.id is not None
                and (
                    getattr(dimension, "is_default_time", False)
                    or dimension.dimension_type == DimensionType.TIME.value
                )
            ),
            None,
        )

        metric_groups: dict[str, list[int]] = {}
        for metric in metrics:
            if metric.id is None:
                continue
            group = getattr(metric, "metric_group", None) or self._infer_metric_group(metric)
            metric_groups.setdefault(group, []).append(metric.id)

        return DatasetProfileRuntime(
            dataset_id=scope.dataset_id,
            business_domain=self._infer_business_domain(metrics, terms),
            core_metric_ids=core_metric_ids,
            metric_groups=metric_groups,
            default_dimensions=[
                dimension.id
                for dimension in dimensions
                if dimension.id is not None and getattr(dimension, "is_default_group_by", False)
            ],
            default_time_dimension=default_time_dimension,
            common_terms=[term.word for term in terms if term.word],
            overview_examples=[example.question for example in examples if example.question and self._is_overview_example(example)],
        )

    def _load_metrics(self, scope: DatasetScope) -> list[SemanticMetric]:
        conditions = [
            SemanticMetric.oid == scope.oid,
            SemanticMetric.datasource_id == scope.datasource_id,
            SemanticMetric.status == AssetStatus.APPROVED.value,
        ]
        if scope.table_ids:
            conditions.append(SemanticMetric.table_id.in_(scope.table_ids))
        return _all(self.session.exec(select(SemanticMetric).where(*conditions)))

    def _load_dimensions(self, scope: DatasetScope) -> list[SemanticDimension]:
        conditions = [
            SemanticDimension.oid == scope.oid,
            SemanticDimension.datasource_id == scope.datasource_id,
            SemanticDimension.status == AssetStatus.APPROVED.value,
        ]
        if scope.table_ids:
            conditions.append(SemanticDimension.table_id.in_(scope.table_ids))
        return _all(self.session.exec(select(SemanticDimension).where(*conditions)))

    def _load_terms(self, scope: DatasetScope) -> list[Terminology]:
        terms = _all(self.session.exec(select(Terminology).where(Terminology.enabled.is_(True))))
        return [
            term
            for term in terms
            if not term.specific_ds or scope.datasource_id in (term.datasource_ids or [])
        ]

    def _load_examples(self, scope: DatasetScope) -> list[DataTraining]:
        conditions = [DataTraining.enabled.is_(True)]
        if scope.datasource_id is not None:
            conditions.append(DataTraining.datasource == scope.datasource_id)
        return _all(self.session.exec(select(DataTraining).where(*conditions)))

    def _infer_metric_group(self, metric: SemanticMetric) -> str:
        text = " ".join([metric.display_name or "", metric.name or "", metric.description or "", " ".join(metric.aliases or [])])
        if any(word in text for word in ("访问", "流量", "UV", "PV")):
            return "流量表现"
        if any(word in text for word in ("咨询", "询盘", "联系")):
            return "咨询表现"
        if any(word in text for word in ("关注", "粉丝")):
            return "关注表现"
        if any(word in text for word in ("转化", "成交", "支付")):
            return "转化表现"
        return "核心指标"

    def _infer_business_domain(self, metrics: list[SemanticMetric], terms: list[Terminology]) -> str | None:
        text = " ".join(
            [metric.display_name or "" for metric in metrics[:8]]
            + [term.word or "" for term in terms[:8]]
        )
        if any(word in text for word in ("档口", "咨询", "访问", "关注")):
            return "档口经营"
        if any(word in text for word in ("订单", "支付", "销售", "成交")):
            return "交易经营"
        return None

    def _is_overview_example(self, example: DataTraining) -> bool:
        example_type = getattr(example, "example_type", None)
        if example_type == "OVERVIEW_EXAMPLE":
            return True
        question = example.question or ""
        return any(word in question for word in ("怎么样", "咋样", "整体", "概览"))


def _all(result: Any) -> list[Any]:
    if hasattr(result, "scalars"):
        return result.scalars().all()
    if hasattr(result, "all"):
        return result.all()
    return list(result or [])

