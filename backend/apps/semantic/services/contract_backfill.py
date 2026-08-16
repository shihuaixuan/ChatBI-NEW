"""存量语义契约的保守回填规划与覆盖率计算。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.semantic.models.orm import (
    MetricDimensionCapability,
    SemanticDataset,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticModelRelation,
)
from apps.semantic.utils.schema_selection import configured_model_ids


@dataclass(frozen=True, slots=True)
class ContractBackfillUpdate:
    """能够由旧字段确定、可安全写入的单项回填。"""

    asset_type: str
    asset_id: int
    values: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ContractBackfillReviewItem:
    """需要人工确认、脚本不得自动写入的业务语义。"""

    asset_type: str
    asset_id: int
    code: str
    detail: str
    candidate: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DatasetContractCoverage:
    """单个数据集的契约覆盖率。"""

    dataset_id: int
    model_grain_rate: float
    metric_contract_rate: float
    dimension_binding_rate: float
    metric_capability_rate: float
    relation_contract_rate: float
    default_time_dimension_rate: float


@dataclass(frozen=True, slots=True)
class SemanticContractBackfillPlan:
    """幂等回填计划；只有 updates 中的确定值允许自动应用。"""

    updates: tuple[ContractBackfillUpdate, ...]
    reviews: tuple[ContractBackfillReviewItem, ...]
    coverage: tuple[DatasetContractCoverage, ...]

    def to_summary(self) -> dict[str, Any]:
        return {
            "update_count": len(self.updates),
            "review_count": len(self.reviews),
            "updates": [
                {
                    "asset_type": item.asset_type,
                    "asset_id": item.asset_id,
                    "values": item.values,
                }
                for item in self.updates
            ],
            "reviews": [
                {
                    "asset_type": item.asset_type,
                    "asset_id": item.asset_id,
                    "code": item.code,
                    "detail": item.detail,
                    "candidate": item.candidate,
                }
                for item in self.reviews
            ],
            "coverage": [
                {
                    "dataset_id": item.dataset_id,
                    "model_grain_rate": item.model_grain_rate,
                    "metric_contract_rate": item.metric_contract_rate,
                    "dimension_binding_rate": item.dimension_binding_rate,
                    "metric_capability_rate": item.metric_capability_rate,
                    "relation_contract_rate": item.relation_contract_rate,
                    "default_time_dimension_rate": item.default_time_dimension_rate,
                }
                for item in self.coverage
            ],
        }


def plan_semantic_contract_backfill(
    *,
    models: list[SemanticModel],
    metrics: list[SemanticMetric],
    dimensions: list[SemanticDimension],
    relations: list[SemanticModelRelation],
    datasets: list[SemanticDataset],
    dataset_model_configs: list[SemanticDatasetModelConfig],
    capabilities: list[MetricDimensionCapability],
) -> SemanticContractBackfillPlan:
    """生成确定性更新、人工审核项和逐数据集覆盖率。"""

    active_models = [item for item in models if item.status == 1 and item.id]
    active_metrics = [item for item in metrics if item.status == 1 and item.id]
    active_dimensions = [item for item in dimensions if item.status == 1 and item.id]
    active_relations = [item for item in relations if item.status == 1 and item.id]
    updates: list[ContractBackfillUpdate] = []
    reviews: list[ContractBackfillReviewItem] = []

    for model in active_models:
        if model.contract_status is None:
            updates.append(
                ContractBackfillUpdate("MODEL", _asset_id(model), {"contract_status": "DRAFT"})
            )
        if model.model_kind is None:
            candidate = "ENTITY" if model.primary_key else (
                "DETAIL" if model.model_grain else "UNKNOWN"
            )
            reviews.append(
                ContractBackfillReviewItem(
                    "MODEL",
                    _asset_id(model),
                    "MODEL_KIND_REVIEW_REQUIRED",
                    "模型类型属于业务语义，必须人工确认",
                    {"model_kind": candidate},
                )
            )
        if not model.row_description:
            reviews.append(
                ContractBackfillReviewItem(
                    "MODEL",
                    _asset_id(model),
                    "MODEL_ROW_DESCRIPTION_REQUIRED",
                    "必须补充一行数据的业务含义",
                    {},
                )
            )

    for dimension in active_dimensions:
        values: dict[str, Any] = {}
        if dimension.binding_role is None:
            values["binding_role"] = (
                "KEY" if dimension.is_primary_key else (
                    "TIME" if dimension.is_default_time else "ATTRIBUTE"
                )
            )
        if dimension.binding_priority is None:
            values["binding_priority"] = 0
        if values:
            updates.append(
                ContractBackfillUpdate("DIMENSION", _asset_id(dimension), values)
            )
        if dimension.logical_dimension_id is None:
            reviews.append(
                ContractBackfillReviewItem(
                    "DIMENSION",
                    _asset_id(dimension),
                    "LOGICAL_DIMENSION_REVIEW_REQUIRED",
                    "同名物理维度不能自动合并为业务维度",
                    {
                        "biz_name": dimension.biz_name,
                        "semantic_type": dimension.semantic_type,
                        "data_type": dimension.data_type,
                    },
                )
            )

    for relation in active_relations:
        if relation.contract_status is None:
            updates.append(
                ContractBackfillUpdate(
                    "RELATION", _asset_id(relation), {"contract_status": "DRAFT"}
                )
            )
        if not relation.cardinality or not relation.metric_propagation:
            reviews.append(
                ContractBackfillReviewItem(
                    "RELATION",
                    _asset_id(relation),
                    "RELATION_CONTRACT_REVIEW_REQUIRED",
                    "关系基数和指标传播方向不能由连接条件自动确认",
                    {},
                )
            )

    dimension_by_id = {_asset_id(item): item for item in active_dimensions}
    capability_keys = {
        (item.metric_id, item.logical_dimension_id)
        for item in capabilities
        if item.status == 1
    }
    for metric in active_metrics:
        if not _metric_contract_complete(metric):
            reviews.append(
                ContractBackfillReviewItem(
                    "METRIC",
                    _asset_id(metric),
                    "METRIC_CONTRACT_REVIEW_REQUIRED",
                    "指标粒度、可加性和时间语义必须人工确认",
                    {"default_agg": metric.default_agg},
                )
            )
        for dimension_id in _legacy_dimension_ids(metric.relate_dimensions):
            legacy_dimension = dimension_by_id.get(dimension_id)
            if legacy_dimension is None or legacy_dimension.model_id != metric.model_id:
                reviews.append(
                    ContractBackfillReviewItem(
                        "METRIC",
                        _asset_id(metric),
                        "CROSS_MODEL_CAPABILITY_REVIEW_REQUIRED",
                        "跨模型维度能力必须补充关系路径、基数和传播方向",
                        {"physical_dimension_id": dimension_id},
                    )
                )
            elif legacy_dimension.logical_dimension_id is None:
                reviews.append(
                    ContractBackfillReviewItem(
                        "METRIC",
                        _asset_id(metric),
                        "CAPABILITY_LOGICAL_DIMENSION_REQUIRED",
                        "物理维度绑定业务维度后才能生成能力契约",
                        {"physical_dimension_id": dimension_id},
                    )
                )
            elif (
                _asset_id(metric), legacy_dimension.logical_dimension_id
            ) not in capability_keys:
                reviews.append(
                    ContractBackfillReviewItem(
                        "METRIC",
                        _asset_id(metric),
                        "CAPABILITY_REVIEW_REQUIRED",
                        "旧关联维度只生成同模型能力候选，不自动标记为安全",
                        {
                            "logical_dimension_id": legacy_dimension.logical_dimension_id,
                            "physical_dimension_id": _asset_id(legacy_dimension),
                            "binding_strategy": "SAME_MODEL",
                        },
                    )
                )

    coverage = _dataset_coverage(
        datasets,
        dataset_model_configs,
        active_models,
        active_metrics,
        active_dimensions,
        active_relations,
        capabilities,
    )
    return SemanticContractBackfillPlan(
        updates=tuple(updates),
        reviews=tuple(reviews),
        coverage=tuple(coverage),
    )


def _dataset_coverage(
    datasets: list[SemanticDataset],
    configs: list[SemanticDatasetModelConfig],
    models: list[SemanticModel],
    metrics: list[SemanticMetric],
    dimensions: list[SemanticDimension],
    relations: list[SemanticModelRelation],
    capabilities: list[MetricDimensionCapability],
) -> list[DatasetContractCoverage]:
    result: list[DatasetContractCoverage] = []
    for dataset in datasets:
        if dataset.status != 1 or dataset.id is None:
            continue
        # 配置表同时承载多个数据集；覆盖率必须只读取当前数据集的模型配置。
        dataset_configs = [
            item for item in configs if item.dataset_id == dataset.id
        ]
        model_ids = set(configured_model_ids(dataset, dataset_configs))
        if not model_ids:
            model_ids = {
                _asset_id(item)
                for item in models
                if item.domain_id == dataset.domain_id
            }
        selected_models = [item for item in models if item.id in model_ids]
        selected_metrics = [item for item in metrics if item.model_id in model_ids]
        selected_dimensions = [
            item for item in dimensions if item.model_id in model_ids
        ]
        selected_relations = [
            item
            for item in relations
            if item.left_model_id in model_ids and item.right_model_id in model_ids
        ]
        metric_ids = {item.id for item in selected_metrics}
        selected_capabilities = [
            item
            for item in capabilities
            if item.status == 1 and item.metric_id in metric_ids
        ]
        expected_capability_count = sum(
            len(_legacy_dimension_ids(item.relate_dimensions))
            for item in selected_metrics
        )
        result.append(
            DatasetContractCoverage(
                dataset_id=dataset.id,
                model_grain_rate=_rate(
                    sum(_model_grain_complete(item) for item in selected_models),
                    len(selected_models),
                ),
                metric_contract_rate=_rate(
                    sum(_metric_contract_complete(item) for item in selected_metrics),
                    len(selected_metrics),
                ),
                dimension_binding_rate=_rate(
                    sum(item.logical_dimension_id is not None for item in selected_dimensions),
                    len(selected_dimensions),
                ),
                metric_capability_rate=_rate(
                    len(selected_capabilities), expected_capability_count
                ),
                relation_contract_rate=_rate(
                    sum(
                        bool(item.cardinality and item.metric_propagation and item.aggregation_safety)
                        for item in selected_relations
                    ),
                    len(selected_relations),
                ),
                default_time_dimension_rate=_rate(
                    sum(
                        item.time_semantics == "NONE"
                        or item.default_time_dimension_id is not None
                        for item in selected_metrics
                    ),
                    len(selected_metrics),
                ),
            )
        )
    return result


def _model_grain_complete(model: SemanticModel) -> bool:
    if model.model_kind == "ENTITY":
        return bool(model.primary_key)
    return model.model_kind in {"FACT", "DETAIL", "SNAPSHOT"} and bool(
        model.model_grain
    )


def _metric_contract_complete(metric: SemanticMetric) -> bool:
    return bool(
        metric.default_agg
        and metric.result_grain
        and metric.additivity
        and metric.time_semantics
        and (
            metric.time_semantics == "NONE"
            or metric.default_time_dimension_id is not None
        )
    )


def _legacy_dimension_ids(values: list[dict[str, Any]]) -> set[int]:
    result: set[int] = set()
    for item in values or []:
        value = item.get("id") or item.get("dimensionId") or item.get("dimension_id")
        if isinstance(value, int) and value > 0:
            result.add(value)
    return result


def _rate(completed: int, total: int) -> float:
    return round(completed / total, 4) if total else 1.0


def _asset_id(asset: object) -> int:
    value = getattr(asset, "id", None)
    if not isinstance(value, int):
        raise ValueError("存量语义资产缺少有效主键")
    return value


__all__ = [
    "ContractBackfillReviewItem",
    "ContractBackfillUpdate",
    "DatasetContractCoverage",
    "SemanticContractBackfillPlan",
    "plan_semantic_contract_backfill",
]
