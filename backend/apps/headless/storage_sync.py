from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from apps.headless.models import (
    HeadlessDataSet,
    HeadlessDataSetAsset,
    HeadlessDataSetModelConfig,
    HeadlessDimension,
    HeadlessDimensionValue,
    HeadlessMetric,
    HeadlessModel,
    HeadlessModelField,
    HeadlessModelMeasure,
)


def unique_texts(items: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_model_source(model: HeadlessModel) -> None:
    detail = dict(model.model_detail or {})
    table_query = detail.get("tableQuery") if isinstance(detail.get("tableQuery"), dict) else {}
    sql_query = detail.get("sqlQuery") if isinstance(detail.get("sqlQuery"), dict) else {}

    if model.table_name is None:
        model.table_name = _clean(table_query.get("table") or table_query.get("tableName") or table_query.get("table_name"))
    if model.sql_query is None:
        model.sql_query = _clean(sql_query.get("sql") or sql_query.get("query"))

    if model.source_type.upper() == "SQL":
        detail["queryType"] = "sql_query"
    else:
        detail["queryType"] = detail.get("queryType") or "table_query"

    if model.table_name:
        table_payload = dict(table_query)
        table_payload["table"] = model.table_name
        if model.database_name:
            table_payload["database"] = model.database_name
        if model.schema_name:
            table_payload["schema"] = model.schema_name
        detail["tableQuery"] = table_payload
    else:
        detail.setdefault("tableQuery", {})

    if model.sql_query:
        sql_payload = dict(sql_query)
        sql_payload["sql"] = model.sql_query
        detail["sqlQuery"] = sql_payload
    else:
        detail.setdefault("sqlQuery", {})

    detail.setdefault("fields", [])
    detail.setdefault("identifiers", [])
    detail.setdefault("dimensions", [])
    detail.setdefault("measures", [])
    detail.setdefault("sqlVariables", [])
    model.model_detail = detail


def model_fields_from_detail(model: HeadlessModel) -> list[HeadlessModelField]:
    detail = model.model_detail or {}
    dimension_names = {_biz_name(item) for item in detail.get("dimensions") or [] if isinstance(item, dict)}
    identifier_names = {_biz_name(item) for item in detail.get("identifiers") or [] if isinstance(item, dict)}
    measure_names = {_biz_name(item) for item in detail.get("measures") or [] if isinstance(item, dict)}
    fields: list[HeadlessModelField] = []

    for index, item in enumerate(detail.get("fields") or []):
        if not isinstance(item, dict):
            continue
        biz_name = _field_biz_name(item)
        if not biz_name:
            continue
        role = "FIELD"
        if biz_name in measure_names:
            role = "MEASURE"
        elif biz_name in identifier_names or biz_name in dimension_names:
            role = "DIMENSION"
        fields.append(
            HeadlessModelField(
                oid=model.oid,
                model_id=model.id or 0,
                field_name=item.get("fieldName") or item.get("field_name") or biz_name,
                name=item.get("name") or item.get("fieldComment") or item.get("field_comment") or biz_name,
                biz_name=biz_name,
                expr=item.get("expr") or biz_name,
                data_type=item.get("dataType") or item.get("data_type"),
                field_role=role,
                semantic_type=item.get("semanticType") or item.get("semantic_type"),
                alias=item.get("alias") or [],
                type_params=item.get("typeParams") or item.get("type_params") or {},
                source_order=index,
            )
        )
    return fields


def model_measures_from_detail(
    model: HeadlessModel,
    fields_by_biz_name: dict[str, HeadlessModelField] | None = None,
) -> list[HeadlessModelMeasure]:
    measures: list[HeadlessModelMeasure] = []
    fields_by_biz_name = fields_by_biz_name or {}
    for item in (model.model_detail or {}).get("measures") or []:
        if not isinstance(item, dict):
            continue
        biz_name = _biz_name(item)
        if not biz_name:
            continue
        field = fields_by_biz_name.get(biz_name)
        measures.append(
            HeadlessModelMeasure(
                oid=model.oid,
                model_id=model.id or 0,
                field_id=field.id if field else None,
                name=item.get("name") or biz_name,
                biz_name=biz_name,
                expr=item.get("expr") or biz_name,
                agg=item.get("agg") or item.get("defaultAgg") or item.get("default_agg") or "SUM",
                data_type=item.get("dataType") or item.get("data_type") or (field.data_type if field else None),
                alias=item.get("alias") or [],
                description=item.get("description"),
                type_params=item.get("typeParams") or item.get("type_params") or {},
            )
        )
    return measures


def build_model_detail_from_storage(
    model: HeadlessModel,
    fields: list[HeadlessModelField],
    measures: list[HeadlessModelMeasure],
) -> dict[str, Any]:
    normalize_model_source(model)
    detail = dict(model.model_detail or {})
    if fields:
        detail["fields"] = [
            {
                "id": field.id,
                "fieldName": field.field_name,
                "dataType": field.data_type,
                "name": field.name,
                "bizName": field.biz_name,
                "expr": field.expr,
                "semanticType": field.semantic_type,
                "alias": field.alias or [],
            }
            for field in sorted(fields, key=lambda item: item.source_order)
            if field.status == 1 and field.is_available
        ]
        detail["identifiers"] = [
            {
                "id": field.id,
                "name": field.name,
                "bizName": field.biz_name,
                "fieldName": field.field_name,
                "type": "primary",
            }
            for field in sorted(fields, key=lambda item: item.source_order)
            if field.status == 1 and field.field_role == "IDENTIFIER"
        ]
    if measures:
        detail["measures"] = [
            {
                "id": measure.id,
                "name": measure.name,
                "bizName": measure.biz_name,
                "expr": measure.expr,
                "agg": measure.agg,
                "alias": measure.alias or [],
                "datasourceId": model.id,
            }
            for measure in measures
            if measure.status == 1
        ]
    return detail


def dimension_values_from_maps(dimension: HeadlessDimension) -> list[HeadlessDimensionValue]:
    values: list[HeadlessDimensionValue] = []
    for item in dimension.dim_value_maps or []:
        if not isinstance(item, dict):
            continue
        value = _clean(item.get("value") or item.get("techName") or item.get("tech_name"))
        if not value:
            continue
        display_value = _clean(item.get("displayValue") or item.get("display_value") or item.get("bizName") or item.get("biz_name"))
        values.append(
            HeadlessDimensionValue(
                oid=dimension.oid,
                dimension_id=dimension.id or 0,
                model_id=dimension.model_id,
                value=value,
                display_value=display_value or value,
                biz_name=_clean(item.get("bizName") or item.get("biz_name")),
                alias=item.get("alias") or [],
                description=item.get("description"),
                source_type=item.get("sourceType") or item.get("source_type") or "MANUAL",
            )
        )
    return values


def build_dim_value_maps_from_storage(values: list[HeadlessDimensionValue]) -> list[dict[str, Any]]:
    return [
        {
            "value": value.value,
            "bizName": value.display_value or value.value,
            "biz_name": value.biz_name,
            "alias": value.alias or [],
        }
        for value in values
        if value.enabled and value.status == 1
    ]


def dataset_model_configs_from_detail(dataset: HeadlessDataSet) -> list[HeadlessDataSetModelConfig]:
    configs: list[HeadlessDataSetModelConfig] = []
    for index, item in enumerate(_dataset_config_items(dataset)):
        model_id = item.get("id") or item.get("model_id") or item.get("modelId")
        if not isinstance(model_id, int):
            continue
        configs.append(
            HeadlessDataSetModelConfig(
                oid=dataset.oid,
                dataset_id=dataset.id or 0,
                model_id=model_id,
                includes_all=bool(item.get("includesAll") or item.get("includes_all")),
                is_default=bool(item.get("isDefault") or item.get("is_default")),
                sort_order=index,
            )
        )
    return configs


def dataset_assets_from_detail(dataset: HeadlessDataSet) -> list[HeadlessDataSetAsset]:
    assets: list[HeadlessDataSetAsset] = []
    for config in _dataset_config_items(dataset):
        model_id = config.get("id") or config.get("model_id") or config.get("modelId")
        if not isinstance(model_id, int):
            continue
        for asset_type, key in [("METRIC", "metrics"), ("DIMENSION", "dimensions")]:
            for index, asset_id in enumerate(config.get(key) or []):
                if isinstance(asset_id, int):
                    assets.append(
                        HeadlessDataSetAsset(
                            oid=dataset.oid,
                            dataset_id=dataset.id or 0,
                            model_id=model_id,
                            asset_type=asset_type,
                            asset_id=asset_id,
                            sort_order=index,
                        )
                    )
    return assets


def build_dataset_detail_from_storage(
    _dataset: HeadlessDataSet,
    configs: list[HeadlessDataSetModelConfig],
    assets: list[HeadlessDataSetAsset],
) -> dict[str, Any]:
    assets_by_model: dict[int, dict[str, list[int]]] = {}
    for asset in assets:
        if asset.status != 1:
            continue
        bucket = assets_by_model.setdefault(asset.model_id, {"metrics": [], "dimensions": []})
        if asset.asset_type == "METRIC" and asset.asset_id not in bucket["metrics"]:
            bucket["metrics"].append(asset.asset_id)
        elif asset.asset_type == "DIMENSION" and asset.asset_id not in bucket["dimensions"]:
            bucket["dimensions"].append(asset.asset_id)

    return {
        "dataSetModelConfigs": [
            {
                "id": config.model_id,
                "includesAll": config.includes_all,
                "metrics": assets_by_model.get(config.model_id, {}).get("metrics", []),
                "dimensions": assets_by_model.get(config.model_id, {}).get("dimensions", []),
            }
            for config in sorted(configs, key=lambda item: item.sort_order)
            if config.status == 1
        ]
    }


def normalize_model_storage_fields(metric: HeadlessMetric) -> None:
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
                item.get("fieldName") or item.get("field_name") or item.get("bizName") or item.get("biz_name")
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
        for item in metric_params.get("metrics", []) if isinstance(metric_params, dict) else []:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("id"), int):
                refs.append(item["id"])
            names.append(item.get("bizName") or item.get("biz_name") or item.get("name"))
        metric.metric_refs = refs
        metric.expr = metric.expr or metric_params.get("expr") or metric.biz_name
        metric.fields = unique_texts(names or [metric.expr])
        return

    measure_params = params.get("metricDefineByMeasureParams") or {}
    if isinstance(measure_params, dict):
        metric.measure_id = metric.measure_id or measure_params.get("measureId") or measure_params.get("measure_id")
        measures = [item for item in measure_params.get("measures", []) if isinstance(item, dict)]
        metric.expr = metric.expr or measure_params.get("expr") or (measures[0].get("expr") if measures else None) or metric.biz_name
        metric.filter_sql = metric.filter_sql or measure_params.get("filterSql") or measure_params.get("filter_sql")
        metric.fields = unique_texts(
            [item.get("expr") or item.get("bizName") or item.get("biz_name") for item in measures] or [metric.expr]
        )
    validate_metric_quality(metric)


def validate_metric_quality(metric: HeadlessMetric) -> None:
    if not metric.expr and not metric.fields:
        metric.quality_status = "INVALID"
        metric.quality_message = "指标表达式或依赖字段不能为空"
        return
    if metric.define_type == "METRIC" and not metric.metric_refs:
        metric.quality_status = "INVALID"
        metric.quality_message = "派生指标必须引用至少一个指标"
        return
    metric.quality_status = "VALID"
    metric.quality_message = ""


def check_storage_consistency(
    model: HeadlessModel | None = None,
    fields: list[HeadlessModelField] | None = None,
    measures: list[HeadlessModelMeasure] | None = None,
    dimension: HeadlessDimension | None = None,
    dimension_values: list[HeadlessDimensionValue] | None = None,
    dataset: HeadlessDataSet | None = None,
    dataset_model_configs: list[HeadlessDataSetModelConfig] | None = None,
    dataset_assets: list[HeadlessDataSetAsset] | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if model is not None:
        json_fields = {_field_biz_name(item) for item in (model.model_detail or {}).get("fields", []) if isinstance(item, dict)}
        storage_fields = {item.biz_name for item in fields or [] if item.status == 1}
        if json_fields != storage_fields:
            issues.append({"type": "MODEL_FIELD_MISMATCH", "json": sorted(json_fields), "storage": sorted(storage_fields)})

        json_measures = {_biz_name(item) for item in (model.model_detail or {}).get("measures", []) if isinstance(item, dict)}
        storage_measures = {item.biz_name for item in measures or [] if item.status == 1}
        if json_measures != storage_measures:
            issues.append({"type": "MODEL_MEASURE_MISMATCH", "json": sorted(json_measures), "storage": sorted(storage_measures)})

    if dimension is not None:
        json_values = {
            _clean(item.get("value") or item.get("techName") or item.get("tech_name"))
            for item in dimension.dim_value_maps or []
            if isinstance(item, dict)
        }
        storage_values = {item.value for item in dimension_values or [] if item.status == 1 and item.enabled}
        if json_values != storage_values:
            issues.append({"type": "DIMENSION_VALUE_MISMATCH", "json": sorted(json_values), "storage": sorted(storage_values)})

    if dataset is not None:
        json_model_ids = {item.model_id for item in dataset_model_configs_from_detail(dataset)}
        storage_model_ids = {item.model_id for item in dataset_model_configs or [] if item.status == 1}
        if json_model_ids != storage_model_ids:
            issues.append(
                {
                    "type": "DATASET_MODEL_CONFIG_MISMATCH",
                    "json": sorted(json_model_ids),
                    "storage": sorted(storage_model_ids),
                }
            )

        json_assets = {(item.asset_type, item.asset_id) for item in dataset_assets_from_detail(dataset)}
        storage_asset_keys = {(item.asset_type, item.asset_id) for item in dataset_assets or [] if item.status == 1}
        if json_assets != storage_asset_keys:
            issues.append(
                {
                    "type": "DATASET_ASSET_MISMATCH",
                    "json": sorted(json_assets),
                    "storage": sorted(storage_asset_keys),
                }
            )
    return issues


def _dataset_config_items(dataset: HeadlessDataSet) -> list[dict[str, Any]]:
    return [
        item
        for item in (dataset.data_set_detail or {}).get("dataSetModelConfigs", [])
        if isinstance(item, dict)
    ]


def _field_biz_name(item: dict[str, Any]) -> str:
    return _clean(item.get("bizName") or item.get("biz_name") or item.get("fieldName") or item.get("field_name"))


def _biz_name(item: dict[str, Any]) -> str:
    return _clean(item.get("bizName") or item.get("biz_name") or item.get("name"))


def _clean(value: Any) -> str:
    return str(value or "").strip()
