from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from apps.datasource import DatasourceRecord
from apps.semantic.models.dto import (
    DatasetSchema,
    JoinRelation,
    Ontology,
    SchemaElement,
)
from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticDomain,
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelMeasure,
    SemanticModelRelation,
    SemanticTerm,
)
from apps.semantic.repository.schema_repository import DatasetSchemaAssets
from apps.semantic.services.builders.metric_builder import (
    measure_with_datasource_id,
    normalize_metric_storage_fields,
)
from apps.semantic.services.builders.model_builder import is_time_dimension_type
from apps.semantic.utils.orm_mapping import (
    build_dim_value_maps_from_storage,
    build_model_detail_from_storage,
    normalize_model_source,
)
from apps.semantic.utils.schema_selection import (
    dataset_model_configs,
    runtime_dataset_configs,
    selected_model_domain_ids,
)
from apps.semantic.utils.semantic_aliases import value_aliases
from apps.semantic.utils.text import unique_texts


class SemanticSchemaBuilder:
    def build(self, assets: DatasetSchemaAssets) -> DatasetSchema:
        """把一次加载得到的资产快照组装为运行时 Schema。"""

        return self.build_from_assets(
            assets.dataset,
            assets.domain,
            assets.models,
            assets.metrics,
            assets.dimensions,
            assets.terms,
            assets.datasources,
            assets.model_relations,
            model_fields=assets.model_fields,
            model_measures=assets.model_measures,
            dimension_values=assets.dimension_values,
            dataset_model_configs=assets.dataset_model_configs,
            dataset_assets=assets.dataset_assets,
            subject_domains=assets.subject_domains,
        )

    def build_from_assets(
        self,
        dataset: SemanticDataset,
        domain: SemanticDomain | None,
        models: list[SemanticModel],
        metrics: list[SemanticMetric],
        dimensions: list[SemanticDimension],
        terms: list[SemanticTerm],
        datasources: list[DatasourceRecord] | None = None,
        model_relations: list[SemanticModelRelation] | None = None,
        model_fields: list[SemanticModelField] | None = None,
        model_measures: list[SemanticModelMeasure] | None = None,
        dimension_values: list[SemanticDimensionValue] | None = None,
        dataset_model_configs: list[SemanticDatasetModelConfig] | None = None,
        dataset_assets: list[SemanticDatasetAsset] | None = None,
        subject_domains: list[SemanticDomain] | None = None,
    ) -> DatasetSchema:
        configs = runtime_dataset_configs(dataset, dataset_model_configs)
        selected_model_ids = {config["id"] for config in configs}
        selected_models = [model for model in models if model.id in selected_model_ids and model.status == 1]
        model_by_id = {model.id: model for model in selected_models}
        metric_ids_by_model, dimension_ids_by_model = _runtime_dataset_asset_ids(dataset, dataset_assets)
        includes_all_models = {config["id"] for config in configs if config["includes_all"]}

        exposed_metrics = [
            metric
            for metric in metrics
            if metric.model_id in model_by_id
            and metric.status == 1
            and metric.quality_status != "INVALID"
            and (metric.model_id in includes_all_models or metric.id in metric_ids_by_model.get(metric.model_id, set()))
        ]
        exposed_dimensions = [
            dimension
            for dimension in dimensions
            if dimension.model_id in model_by_id
            and dimension.status == 1
            and (
                dimension.model_id in includes_all_models
                or dimension.id in dimension_ids_by_model.get(dimension.model_id, set())
            )
        ]
        datasource_by_id = {
            datasource.id: datasource
            for datasource in datasources or []
            if datasource.id is not None
        }
        database_type, database_version = _runtime_database_info(selected_models, datasource_by_id)
        exposed_relations = _runtime_join_relations(model_relations or [], model_by_id)
        fields_by_model = _group_by_model(model_fields or [])
        measures_by_model = _group_by_model(model_measures or [])
        values_by_dimension = _group_values_by_dimension(dimension_values or [])

        data_set_element = SchemaElement(
            data_set_id=dataset.id or 0,
            data_set_name=dataset.name,
            id=dataset.id or 0,
            name=dataset.name,
            biz_name=dataset.biz_name,
            type="DATASET",
            alias=dataset.alias or [],
            description=dataset.description or (domain.description if domain else None),
        )
        schema = DatasetSchema(
            database_type=database_type,
            database_version=database_version,
            data_set=data_set_element,
            subject_domains=_runtime_subject_domains(selected_models, subject_domains or ([domain] if domain else [])),
            models=[self._model_runtime(model, fields_by_model.get(model.id, []), measures_by_model.get(model.id, [])) for model in selected_models],
            model_relations=exposed_relations,
            metrics=[self._metric_element(dataset, metric) for metric in exposed_metrics],
            dimensions=[self._dimension_element(dataset, dimension, model_by_id.get(dimension.model_id)) for dimension in exposed_dimensions],
            terms=[
                self._term_element(dataset, term)
                for term in terms
                if term.status == 1 and _term_applies_to_dataset(term, dataset)
            ],
            query_config=dataset.query_config or {},
        )
        schema.dimension_values = [
            self._dimension_value_element(
                dataset,
                dimension,
                model_by_id.get(dimension.model_id),
                values_by_dimension.get(dimension.id, []),
            )
            for dimension in exposed_dimensions
            if dimension.dim_value_maps or values_by_dimension.get(dimension.id)
        ]
        schema.metrics.sort(key=lambda item: item.id)
        schema.dimensions.sort(key=lambda item: item.id)
        schema.dimension_values.sort(key=lambda item: item.id)
        schema.terms.sort(key=lambda item: item.id)
        return schema

    def _model_runtime(
        self,
        model: SemanticModel,
        fields: list[SemanticModelField] | None = None,
        measures: list[SemanticModelMeasure] | None = None,
    ) -> dict[str, Any]:
        normalize_model_source(model)
        if fields or measures:
            detail = build_model_detail_from_storage(model, fields or [], measures or [])
        else:
            detail = model.model_detail or {}
        table_query = model.table_name or _runtime_table_query(detail.get("tableQuery"))
        sql_query = _runtime_sql_query(model.sql_query or detail.get("sqlQuery"))
        return {
            "id": model.id,
            "name": model.name,
            "biz_name": model.biz_name,
            "datasource_id": model.datasource_id,
            "source_type": model.source_type,
            "default_time_field": model.default_time_field,
            "queryType": detail.get("queryType") or ("sql_query" if model.source_type.upper() == "SQL" else "table_query"),
            "tableQuery": table_query,
            "sqlQuery": sql_query,
            "filterSql": model.filter_sql or "",
            "depends": model.depends or [],
            "fields": detail.get("fields") or [],
            "identifiers": detail.get("identifiers") or [],
            "dimensions": [_dimension_for_runtime(item) for item in detail.get("dimensions") or []],
            "measures": [
                measure_with_datasource_id(item, model.id)
                for item in detail.get("measures") or []
            ],
        }

    def _metric_element(self, dataset: SemanticDataset, metric: SemanticMetric) -> SchemaElement:
        normalize_metric_storage_fields(metric)
        # 检索投影必须在统一入口识别敏感资产，不能依赖下游猜测。
        ext_info = {**(metric.ext or {}), "sensitive_level": metric.sensitive_level}
        return SchemaElement(
            data_set_id=dataset.id or 0,
            data_set_name=dataset.name,
            model=metric.model_id,
            id=metric.id or 0,
            name=metric.name,
            biz_name=metric.biz_name,
            type="METRIC",
            alias=metric.alias or [],
            related_schema_elements=metric.relate_dimensions or [],
            default_agg=metric.default_agg,
            data_format_type=metric.data_format_type,
            is_tag=metric.is_tag,
            description=metric.description,
            ext_info=ext_info,
            type_params=_metric_type_params_for_runtime(metric),
            fields=metric.fields or _metric_fields(metric),
        )

    def _dimension_element(
        self,
        dataset: SemanticDataset,
        dimension: SemanticDimension,
        model: SemanticModel | None,
    ) -> SchemaElement:
        ext_info = {
            **(dimension.ext or {}),
            "dimension_type": dimension.type,
            "sensitive_level": dimension.sensitive_level,
        }
        if dimension.field_id is not None:
            ext_info["field_id"] = dimension.field_id
        if dimension.field_name:
            ext_info["field_name"] = dimension.field_name
        ext_info["is_primary_key"] = dimension.is_primary_key
        ext_info["is_default_time"] = dimension.is_default_time
        if dimension.time_granularities:
            ext_info["time_granularities"] = dimension.time_granularities
        if dimension.data_type:
            ext_info["dimension_data_type"] = dimension.data_type
        elif model:
            ext_info["dimension_data_type"] = _model_field_type(model, dimension.biz_name)
        return SchemaElement(
            data_set_id=dataset.id or 0,
            data_set_name=dataset.name,
            model=dimension.model_id,
            id=dimension.id or 0,
            name=dimension.name,
            biz_name=dimension.biz_name,
            type="DIMENSION",
            alias=dimension.alias or [],
            schema_value_maps=dimension.dim_value_maps or [],
            is_tag=dimension.is_tag,
            description=dimension.description,
            ext_info=ext_info,
            type_params=dimension.type_params or {},
        )

    def _dimension_value_element(
        self,
        dataset: SemanticDataset,
        dimension: SemanticDimension,
        model: SemanticModel | None,
        values: list[SemanticDimensionValue] | None = None,
    ) -> SchemaElement:
        aliases: list[str] = []
        schema_value_maps = build_dim_value_maps_from_storage(values or []) or dimension.dim_value_maps or []
        for item in schema_value_maps:
            aliases.extend(value_aliases(item))
        element = self._dimension_element(dataset, dimension, model)
        element.type = "VALUE"
        element.alias = unique_texts(aliases)
        element.schema_value_maps = schema_value_maps
        return element

    def _term_element(self, dataset: SemanticDataset, term: SemanticTerm) -> SchemaElement:
        return SchemaElement(
            data_set_id=dataset.id or 0,
            data_set_name=dataset.name,
            model=-1,
            id=term.id or 0,
            name=term.name,
            biz_name=term.name,
            type="TERM",
            alias=term.alias or [],
            description=term.description,
            related_schema_elements=[
                *[{"type": "METRIC", "id": metric_id} for metric_id in term.related_metrics or []],
                *[{"type": "DIMENSION", "id": dimension_id} for dimension_id in term.related_dimensions or []],
            ],
        )


def _runtime_subject_domains(
    models: list[SemanticModel],
    domains: Sequence[SemanticDomain | None],
) -> list[dict[str, Any]]:
    domains_by_id = {domain.id: domain for domain in domains if domain is not None and domain.id is not None}
    model_ids_by_domain: dict[int, list[int]] = {}
    for model in models:
        if model.id is None:
            continue
        model_ids_by_domain.setdefault(model.domain_id, []).append(model.id)

    result: list[dict[str, Any]] = []
    for domain_id in selected_model_domain_ids(models):
        model_ids = model_ids_by_domain.get(domain_id) or []
        if not model_ids:
            continue
        domain = domains_by_id.get(domain_id)
        result.append(
            {
                "domain_id": domain_id,
                "name": domain.name if domain else str(domain_id),
                "biz_name": domain.biz_name if domain else str(domain_id),
                "description": domain.description if domain else None,
                "model_ids": model_ids,
            }
        )
    return result


def _term_applies_to_dataset(
    term: SemanticTerm,
    dataset: SemanticDataset,
) -> bool:
    """未指定数据集的术语作用于整个主题域，否则只作用于明确的数据集。"""

    return not term.related_datasets or dataset.id in term.related_datasets


def _runtime_dataset_asset_ids(
    dataset: SemanticDataset,
    storage_assets: list[SemanticDatasetAsset] | None = None,
) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    active_assets = [item for item in storage_assets or [] if item.status == 1]
    if active_assets:
        metric_ids_by_model: dict[int, set[int]] = {}
        dimension_ids_by_model: dict[int, set[int]] = {}
        for asset in active_assets:
            if asset.asset_type == "METRIC":
                metric_ids_by_model.setdefault(asset.model_id, set()).add(asset.asset_id)
            elif asset.asset_type == "DIMENSION":
                dimension_ids_by_model.setdefault(asset.model_id, set()).add(asset.asset_id)
        return metric_ids_by_model, dimension_ids_by_model
    configs = dataset_model_configs(dataset)
    return (
        {config.id: set(config.metrics) for config in configs if not config.includes_all},
        {config.id: set(config.dimensions) for config in configs if not config.includes_all},
    )


def _group_by_model(items: list[SemanticModelField] | list[SemanticModelMeasure]) -> dict[int | None, list[Any]]:
    grouped: dict[int | None, list[Any]] = {}
    for item in items:
        grouped.setdefault(item.model_id, []).append(item)
    return grouped


def _group_values_by_dimension(items: list[SemanticDimensionValue]) -> dict[int | None, list[SemanticDimensionValue]]:
    grouped: dict[int | None, list[SemanticDimensionValue]] = {}
    for item in items:
        if item.enabled and item.status == 1:
            grouped.setdefault(item.dimension_id, []).append(item)
    return grouped


def _model_field_type(model: SemanticModel, field_name: str) -> str | None:
    for field in (model.model_detail or {}).get("fields", []):
        if field.get("fieldName") == field_name or field.get("field_name") == field_name:
            value = field.get("dataType") or field.get("data_type")
            return str(value) if value is not None else None
    return None


def build_ontology_from_schema(schema: DatasetSchema) -> Ontology:
    model_map = {str(model.get("biz_name") or model.get("name")): model for model in schema.models}
    model_name_by_id = {model.get("id"): str(model.get("biz_name") or model.get("name")) for model in schema.models}
    metric_map: dict[str, list[SchemaElement]] = {}
    dimension_map: dict[str, list[SchemaElement]] = {}
    for metric in schema.metrics:
        model_name = model_name_by_id.get(metric.model)
        if model_name:
            metric_map.setdefault(model_name, []).append(metric)
    for dimension in schema.dimensions:
        model_name = model_name_by_id.get(dimension.model)
        if model_name:
            dimension_map.setdefault(model_name, []).append(dimension)
    return Ontology(
        database_type=schema.database_type,
        database_version=schema.database_version,
        model_map=model_map,
        metric_map=metric_map,
        dimension_map=dimension_map,
        join_relations=schema.model_relations,
    )


def _runtime_table_query(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("table") or raw.get("tableName") or raw.get("table_name") or "").strip()
    return str(raw or "").strip()


def _runtime_sql_query(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("sql") or raw.get("query") or "").strip().rstrip(";")
    return str(raw or "").strip().rstrip(";")


def _runtime_database_info(
    models: list[SemanticModel],
    datasource_by_id: dict[int, DatasourceRecord],
) -> tuple[str | None, str | None]:
    for model in models:
        datasource = datasource_by_id.get(model.datasource_id)
        if datasource is None:
            continue
        database_type = datasource.type or datasource.type_name
        return database_type, _datasource_database_version(datasource)
    return None, None


def _datasource_database_version(datasource: DatasourceRecord) -> str | None:
    try:
        config = json.loads(datasource.configuration or "{}")
    except (TypeError, ValueError):
        config = {}
    for key in ["databaseVersion", "database_version", "dbVersion", "db_version", "version"]:
        value = config.get(key)
        if value:
            return str(value)
    return None


def _runtime_join_relations(
    relations: list[SemanticModelRelation],
    model_by_id: dict[int | None, SemanticModel],
) -> list[JoinRelation]:
    runtime_relations: list[JoinRelation] = []
    for relation in relations:
        left_model = model_by_id.get(relation.left_model_id)
        right_model = model_by_id.get(relation.right_model_id)
        if left_model is None or right_model is None:
            continue
        runtime_relations.append(
            JoinRelation(
                id=relation.id,
                left=left_model.biz_name,
                right=right_model.biz_name,
                join_type=_normalize_join_type(relation.join_type),
                join_condition=[_runtime_join_condition(item) for item in relation.join_conditions or []],
            )
        )
    return runtime_relations


def _runtime_join_condition(item: dict[str, Any]) -> list[str]:
    left_field = item.get("leftField") or item.get("left_field") or item.get("left")
    operator = item.get("operator") or item.get("op") or "="
    right_field = item.get("rightField") or item.get("right_field") or item.get("right")
    return [str(left_field or "").strip(), str(operator or "=").strip(), str(right_field or "").strip()]


def _normalize_join_type(join_type: str | None) -> str:
    value = str(join_type or "").strip().lower().replace("_", " ")
    if value in {"left", "left join"}:
        return "left join"
    if value in {"right", "right join"}:
        return "right join"
    if value in {"full", "full join", "full outer join"}:
        return "full join"
    if value in {"inner", "join", "inner join"}:
        return "inner join"
    return value or "left join"


def _metric_type_params_for_runtime(metric: SemanticMetric) -> dict[str, Any]:
    params = dict(metric.type_params or {})
    measure_params = params.get("metricDefineByMeasureParams")
    if isinstance(measure_params, dict):
        measure_params = dict(measure_params)
        measure_params["measures"] = [
            measure_with_datasource_id(item, metric.model_id)
            for item in measure_params.get("measures", [])
            if isinstance(item, dict)
        ]
        params["metricDefineByMeasureParams"] = measure_params
    return params


def _metric_fields(metric: SemanticMetric) -> list[str]:
    params = dict(metric.type_params or {})
    define_type = params.get("metricDefineType") or metric.define_type or "MEASURE"
    if define_type == "FIELD":
        field_params = params.get("metricDefineByFieldParams") or {}
        return unique_texts(
            [
                field.get("fieldName") or field.get("field_name") or field.get("bizName") or field.get("biz_name")
                for field in field_params.get("fields", [])
                if isinstance(field, dict)
            ]
            or [field_params.get("expr") or metric.biz_name]
        )
    if define_type == "METRIC":
        metric_params = params.get("metricDefineByMetricParams") or {}
        return unique_texts(
            [
                item.get("bizName") or item.get("biz_name") or item.get("name")
                for item in metric_params.get("metrics", [])
                if isinstance(item, dict)
            ]
            or [metric_params.get("expr") or metric.biz_name]
        )
    measure_params = params.get("metricDefineByMeasureParams") or {}
    measures = measure_params.get("measures", []) if isinstance(measure_params, dict) else []
    return unique_texts(
        [
            item.get("expr") or item.get("bizName") or item.get("biz_name")
            for item in measures
            if isinstance(item, dict)
        ]
        or [metric.biz_name]
    )


def _dimension_for_runtime(item: dict[str, Any]) -> dict[str, Any]:
    dimension = dict(item)
    dimension_type = dimension.get("type") or "categorical"
    if is_time_dimension_type(dimension_type):
        dimension.setdefault("dateFormat", "yyyy-MM-dd")
        dimension.setdefault("typeParams", {"isPrimary": "true", "timeGranularity": "day"})
    return dimension
