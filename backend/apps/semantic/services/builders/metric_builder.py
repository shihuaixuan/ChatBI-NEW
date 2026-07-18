from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.semantic.models.orm import SemanticMetric, SemanticModel, SemanticModelMeasure
from apps.semantic.services.rules.metric_quality import validate_metric_quality
from apps.semantic.utils.text import unique_texts


@dataclass
class SemanticMetricBuildResult:
    metrics: list[SemanticMetric]
    skipped: list[str]


def build_metrics_from_model_measures(
    model: SemanticModel,
    oid: int,
    measure_biz_names: list[str] | None = None,
    measure_ids: list[int] | None = None,
    storage_measures: list[SemanticModelMeasure] | None = None,
    existing_metrics: list[SemanticMetric] | None = None,
) -> SemanticMetricBuildResult:
    """根据模型度量创建尚不存在的原子指标。"""
    active_storage_measures = [
        item for item in storage_measures or [] if item.status == 1
    ]
    measures = (
        [_measure_detail_from_storage(item) for item in active_storage_measures]
        if active_storage_measures
        else (model.model_detail or {}).get("measures") or []
    )
    measure_by_biz = {
        biz_name: item
        for item in measures
        if isinstance(item, dict)
        for biz_name in [_measure_biz_name(item)]
        if biz_name
    }
    measure_by_id = {
        item.id: item
        for item in active_storage_measures
        if item.id is not None
    }
    requested = unique_texts(measure_biz_names or [])
    if measure_ids:
        requested_measure_items = [
            _measure_detail_from_storage(measure_by_id[item])
            for item in measure_ids
            if item in measure_by_id
        ]
        for item in requested_measure_items:
            measure_by_biz[_measure_biz_name(item)] = item
        target_biz_names = [
            _measure_biz_name(item) for item in requested_measure_items
        ]
    else:
        target_biz_names = requested or [
            _measure_biz_name(item)
            for item in measures
            if isinstance(item, dict)
        ]
    existing_biz_names = {
        metric.biz_name
        for metric in existing_metrics or []
        if metric.status == 1
    }
    metrics: list[SemanticMetric] = []
    skipped: list[str] = []

    for biz_name in unique_texts(target_biz_names):
        measure = measure_by_biz.get(biz_name)
        if not measure or biz_name in existing_biz_names:
            skipped.append(biz_name)
            continue
        metrics.append(
            _metric_from_measure_detail(
                measure_with_datasource_id(measure, model.id),
                oid=oid,
                model_id=model.id or 0,
            )
        )

    return SemanticMetricBuildResult(metrics=metrics, skipped=skipped)


def measure_with_datasource_id(
    item: dict[str, Any], model_id: int | None
) -> dict[str, Any]:
    """补充语义运行时要求的度量数据源标识。"""
    measure = dict(item)
    if model_id is not None and not measure.get("datasourceId"):
        measure["datasourceId"] = model_id
    return measure


def normalize_metric_storage_fields(metric: SemanticMetric) -> None:
    """将兼容格式中的指标定义归一化为 ORM 字段。"""

    if metric.fields:
        validate_metric_quality(metric)
        return
    params = dict(metric.type_params or {})
    define_type = params.get("metricDefineType") or metric.define_type or "MEASURE"
    metric.define_type = define_type
    if define_type == "FIELD":
        field_params = params.get("metricDefineByFieldParams") or {}
        metric.expr = metric.expr or field_params.get("expr") or metric.biz_name
        metric.fields = unique_texts(
            [
                item.get("fieldName")
                or item.get("field_name")
                or item.get("bizName")
                or item.get("biz_name")
                for item in field_params.get("fields", [])
                if isinstance(item, dict)
            ]
            or [metric.expr]
        )
        return
    if define_type == "METRIC":
        metric_params = params.get("metricDefineByMetricParams") or {}
        refs: list[int] = []
        names: list[str] = []
        for item in (
            metric_params.get("metrics", [])
            if isinstance(metric_params, dict)
            else []
        ):
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("id"), int):
                refs.append(item["id"])
            names.append(
                item.get("bizName") or item.get("biz_name") or item.get("name")
            )
        metric.metric_refs = refs
        metric.expr = metric.expr or metric_params.get("expr") or metric.biz_name
        metric.fields = unique_texts(names or [metric.expr])
        return

    measure_params = params.get("metricDefineByMeasureParams") or {}
    if isinstance(measure_params, dict):
        metric.measure_id = (
            metric.measure_id
            or measure_params.get("measureId")
            or measure_params.get("measure_id")
        )
        measures = [
            item
            for item in measure_params.get("measures", [])
            if isinstance(item, dict)
        ]
        metric.expr = (
            metric.expr
            or measure_params.get("expr")
            or (measures[0].get("expr") if measures else None)
            or metric.biz_name
        )
        metric.filter_sql = (
            metric.filter_sql
            or measure_params.get("filterSql")
            or measure_params.get("filter_sql")
        )
        metric.fields = unique_texts(
            [
                item.get("expr") or item.get("bizName") or item.get("biz_name")
                for item in measures
            ]
            or [metric.expr]
        )
    validate_metric_quality(metric)


def _metric_from_measure_detail(
    item: dict[str, Any], oid: int, model_id: int
) -> SemanticMetric:
    biz_name = _measure_biz_name(item)
    default_agg = (
        item.get("agg")
        or item.get("defaultAgg")
        or item.get("default_agg")
        or "SUM"
    )
    expr = item.get("expr") or biz_name
    measure_id = item.get("id")
    # 按 Supersonic Semantic 的指标定义结构记录“由模型度量定义指标”的来源。
    type_params = {
        "metricDefineType": "MEASURE",
        "metricDefineByMeasureParams": {
            "measures": [item],
            "expr": biz_name,
            "filterSql": item.get("constraint") or "",
        },
    }
    if isinstance(measure_id, int):
        type_params["metricDefineByMeasureParams"]["measureId"] = measure_id
    return SemanticMetric(
        oid=oid,
        model_id=model_id,
        name=item.get("name") or biz_name,
        biz_name=biz_name,
        alias=item.get("alias") or [],
        default_agg=default_agg,
        type="ATOMIC",
        define_type="MEASURE",
        measure_id=measure_id if isinstance(measure_id, int) else None,
        expr=expr,
        filter_sql=item.get("constraint") or "",
        fields=unique_texts([expr]),
        type_params=type_params,
        description=item.get("description"),
    )


def _measure_detail_from_storage(measure: SemanticModelMeasure) -> dict[str, Any]:
    return {
        "id": measure.id,
        "name": measure.name,
        "bizName": measure.biz_name,
        "expr": measure.expr,
        "agg": measure.agg,
        "alias": measure.alias or [],
        "description": measure.description,
    }


def _measure_biz_name(item: dict[str, Any]) -> str:
    return str(item.get("bizName") or item.get("biz_name") or "").strip()
