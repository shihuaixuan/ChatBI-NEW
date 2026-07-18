from __future__ import annotations

from typing import Any

from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticModel,
    SemanticModelField,
    SemanticModelMeasure,
)


def normalize_model_source(model: SemanticModel) -> None:
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


def model_fields_from_detail(model: SemanticModel) -> list[SemanticModelField]:
    detail = model.model_detail or {}
    dimension_names = {_biz_name(item) for item in detail.get("dimensions") or [] if isinstance(item, dict)}
    identifier_names = {_biz_name(item) for item in detail.get("identifiers") or [] if isinstance(item, dict)}
    measure_names = {_biz_name(item) for item in detail.get("measures") or [] if isinstance(item, dict)}
    fields: list[SemanticModelField] = []

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
            SemanticModelField(
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
    model: SemanticModel,
    fields_by_biz_name: dict[str, SemanticModelField] | None = None,
) -> list[SemanticModelMeasure]:
    measures: list[SemanticModelMeasure] = []
    fields_by_biz_name = fields_by_biz_name or {}
    for item in (model.model_detail or {}).get("measures") or []:
        if not isinstance(item, dict):
            continue
        biz_name = _biz_name(item)
        if not biz_name:
            continue
        field = fields_by_biz_name.get(biz_name)
        measures.append(
            SemanticModelMeasure(
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
    model: SemanticModel,
    fields: list[SemanticModelField],
    measures: list[SemanticModelMeasure],
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


def dimension_values_from_maps(dimension: SemanticDimension) -> list[SemanticDimensionValue]:
    values: list[SemanticDimensionValue] = []
    for item in dimension.dim_value_maps or []:
        if not isinstance(item, dict):
            continue
        value = _clean(item.get("value") or item.get("techName") or item.get("tech_name"))
        if not value:
            continue
        display_value = _clean(item.get("displayValue") or item.get("display_value") or item.get("bizName") or item.get("biz_name"))
        values.append(
            SemanticDimensionValue(
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


def build_dim_value_maps_from_storage(values: list[SemanticDimensionValue]) -> list[dict[str, Any]]:
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


def dataset_model_configs_from_detail(dataset: SemanticDataset) -> list[SemanticDatasetModelConfig]:
    configs: list[SemanticDatasetModelConfig] = []
    for index, item in enumerate(_dataset_config_items(dataset)):
        model_id = item.get("id") or item.get("model_id") or item.get("modelId")
        if not isinstance(model_id, int):
            continue
        configs.append(
            SemanticDatasetModelConfig(
                oid=dataset.oid,
                dataset_id=dataset.id or 0,
                model_id=model_id,
                includes_all=bool(item.get("includesAll") or item.get("includes_all")),
                is_default=bool(item.get("isDefault") or item.get("is_default")),
                sort_order=index,
            )
        )
    return configs


def dataset_assets_from_detail(dataset: SemanticDataset) -> list[SemanticDatasetAsset]:
    assets: list[SemanticDatasetAsset] = []
    for config in _dataset_config_items(dataset):
        model_id = config.get("id") or config.get("model_id") or config.get("modelId")
        if not isinstance(model_id, int):
            continue
        for asset_type, key in [("METRIC", "metrics"), ("DIMENSION", "dimensions")]:
            for index, asset_id in enumerate(config.get(key) or []):
                if isinstance(asset_id, int):
                    assets.append(
                        SemanticDatasetAsset(
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
    _dataset: SemanticDataset,
    configs: list[SemanticDatasetModelConfig],
    assets: list[SemanticDatasetAsset],
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


def _dataset_config_items(dataset: SemanticDataset) -> list[dict[str, Any]]:
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
