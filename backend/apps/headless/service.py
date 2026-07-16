from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from apps.datasource.models.datasource import CoreDatasource
from apps.headless.models import (
    HeadlessDataSet,
    HeadlessDataSetAsset,
    HeadlessDataSetModelConfig,
    HeadlessDimension,
    HeadlessDimensionValue,
    HeadlessDomain,
    HeadlessMetric,
    HeadlessModel,
    HeadlessModelField,
    HeadlessModelMeasure,
    HeadlessModelRelation,
    HeadlessTerm,
)
from apps.headless.schemas import (
    DataSetModelConfig,
    DataSetSchema,
    HeadlessColumnMeta,
    JoinRelation,
    ModelBuildField,
    ModelBuildSchemaResult,
    ModelCreateWithAssetsPayload,
    Ontology,
    SchemaElement,
    SchemaElementMatch,
    SchemaMapInfo,
)
from apps.headless.storage_sync import (
    build_dim_value_maps_from_storage,
    build_model_detail_from_storage,
    normalize_model_source,
    normalize_model_storage_fields,
    unique_texts,
)


def _unique(items: Iterable[str | None]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _dataset_configs(dataset: HeadlessDataSet) -> list[DataSetModelConfig]:
    raw_configs = (dataset.data_set_detail or {}).get("dataSetModelConfigs") or []
    return [DataSetModelConfig.model_validate(item) for item in raw_configs]


@dataclass
class HeadlessModelAssetBundle:
    model: HeadlessModel
    dimensions: list[HeadlessDimension]
    metrics: list[HeadlessMetric]


@dataclass
class HeadlessMetricBuildResult:
    metrics: list[HeadlessMetric]
    skipped: list[str]


class HeadlessModelBuilder:
    def build_table_schema(
        self,
        table_name: str | None,
        columns: list[HeadlessColumnMeta],
        source_type: str = "TABLE",
        sql: str | None = None,
        datasource_id: int | None = None,
    ) -> ModelBuildSchemaResult:
        fields = [self._build_field(column) for column in columns if column.checked]
        model_detail = {
            "queryType": "sql_query" if source_type.upper() == "SQL" else "table_query",
            "tableQuery": {"table": table_name} if table_name else {},
            "sqlQuery": {"sql": sql} if sql else {},
            "fields": [self._field_detail(field) for field in fields],
            "identifiers": [self._identifier_detail(field) for field in fields if field.role == "IDENTIFIER"],
            "dimensions": [self._dimension_detail(field) for field in fields if field.role in {"IDENTIFIER", "DIMENSION"}],
            "measures": [self._measure_detail(field, datasource_id=datasource_id) for field in fields if field.role == "MEASURE"],
            "sqlVariables": [],
        }
        return ModelBuildSchemaResult(
            source_type=source_type,
            table_name=table_name,
            sql=sql,
            fields=fields,
            model_detail=model_detail,
        )

    def _build_field(self, column: HeadlessColumnMeta) -> ModelBuildField:
        field_name = column.field_name
        data_type = column.field_type or ""
        role = _infer_field_role(field_name, data_type)
        return ModelBuildField(
            field_name=field_name,
            data_type=data_type,
            name=_display_name(column.field_comment, field_name),
            biz_name=field_name,
            expr=field_name,
            role=role,
            default_agg="SUM" if role == "MEASURE" else None,
            semantic_type="time" if _is_time_type(data_type) else None,
            create_asset=role in {"IDENTIFIER", "DIMENSION", "MEASURE"},
        )

    def _field_detail(self, field: ModelBuildField) -> dict[str, Any]:
        return {
            "fieldName": field.field_name,
            "dataType": field.data_type,
            "name": field.name,
            "bizName": field.biz_name,
            "expr": field.expr,
        }

    def _identifier_detail(self, field: ModelBuildField) -> dict[str, Any]:
        return {
            "name": field.name,
            "bizName": field.biz_name,
            "fieldName": field.field_name,
            "type": "primary",
        }

    def _dimension_detail(self, field: ModelBuildField) -> dict[str, Any]:
        dimension_type = _dimension_type_from_field(field)
        detail = {
            "name": field.name,
            "bizName": field.biz_name,
            "expr": field.expr,
            "dataType": field.data_type,
            "type": dimension_type,
            "semanticType": field.semantic_type,
            "alias": field.alias,
            "createDimension": field.create_asset,
        }
        if _is_time_dimension_type(dimension_type):
            detail["dateFormat"] = "yyyy-MM-dd"
            detail["typeParams"] = {"isPrimary": "true", "timeGranularity": "day"}
        return detail

    def _measure_detail(self, field: ModelBuildField, datasource_id: int | None = None) -> dict[str, Any]:
        detail = {
            "name": field.name,
            "bizName": field.biz_name,
            "expr": field.expr,
            "agg": field.default_agg or "SUM",
            "alias": field.alias,
            "isCreateMetric": 0,
            "createMetric": False,
        }
        if datasource_id is not None:
            detail["datasourceId"] = datasource_id
        return detail


def _dimension_type_from_field(field: ModelBuildField) -> str:
    if field.role == "IDENTIFIER":
        return "primary_key"
    if field.semantic_type == "time":
        return "partition_time"
    return "categorical"


def _is_time_dimension_type(dimension_type: str | None) -> bool:
    return dimension_type in {"time", "partition_time"}


def build_model_with_assets(payload: ModelCreateWithAssetsPayload, oid: int = 0) -> HeadlessModelAssetBundle:
    model_detail = _normalize_model_detail(payload)
    model = HeadlessModel(
        oid=oid,
        domain_id=payload.domain_id,
        datasource_id=payload.datasource_id,
        name=payload.name,
        biz_name=payload.biz_name,
        description=payload.description,
        model_detail=model_detail,
        filter_sql=payload.filter_sql,
        alias=payload.alias,
        source_type=payload.source_type,
        depends=payload.depends,
        table_name=payload.table_name,
        sql_query=payload.sql,
    )
    normalize_model_source(model)
    dimensions = [_dimension_from_detail(item, oid=oid, model_id=0) for item in model_detail.get("dimensions", []) if item.get("createDimension", True)]
    return HeadlessModelAssetBundle(model=model, dimensions=dimensions, metrics=[])


def build_metrics_from_model_measures(
    model: HeadlessModel,
    oid: int,
    measure_biz_names: list[str] | None = None,
    measure_ids: list[int] | None = None,
    storage_measures: list[HeadlessModelMeasure] | None = None,
    existing_metrics: list[HeadlessMetric] | None = None,
) -> HeadlessMetricBuildResult:
    storage_measures = [item for item in storage_measures or [] if item.status == 1]
    measures = [_measure_detail_from_storage(item) for item in storage_measures] if storage_measures else (model.model_detail or {}).get("measures") or []
    measure_by_biz = {
        biz_name: item
        for item in measures
        if isinstance(item, dict)
        for biz_name in [_measure_biz_name(item)]
        if biz_name
    }
    measure_by_id = {item.id: item for item in storage_measures if item.id is not None}
    requested = _unique(measure_biz_names or [])
    if measure_ids:
        requested_measure_items = [_measure_detail_from_storage(measure_by_id[item]) for item in measure_ids if item in measure_by_id]
        for item in requested_measure_items:
            measure_by_biz[_measure_biz_name(item)] = item
        target_biz_names = [_measure_biz_name(item) for item in requested_measure_items]
    else:
        target_biz_names = requested or [_measure_biz_name(item) for item in measures if isinstance(item, dict)]
    existing_biz_names = {metric.biz_name for metric in existing_metrics or [] if metric.status == 1}
    metrics: list[HeadlessMetric] = []
    skipped: list[str] = []

    for biz_name in _unique(target_biz_names):
        measure = measure_by_biz.get(biz_name)
        if not measure or biz_name in existing_biz_names:
            skipped.append(biz_name)
            continue
        metrics.append(_metric_from_measure_detail(_measure_with_datasource_id(measure, model.id), oid=oid, model_id=model.id or 0))

    return HeadlessMetricBuildResult(metrics=metrics, skipped=skipped)


class HeadlessSchemaBuilder:
    def __init__(self, session: Any | None = None):
        self.session = session

    def build_dataset_schema(self, oid: int, dataset_id: int) -> DataSetSchema:
        if self.session is None:
            raise ValueError("HEADLESS_SESSION_REQUIRED")
        dataset = self.session.get(HeadlessDataSet, dataset_id)
        if dataset is None or dataset.oid != oid or dataset.status != 1:
            raise ValueError("HEADLESS_DATASET_NOT_FOUND")
        domain = self.session.get(HeadlessDomain, dataset.domain_id)
        dataset_model_configs = _all(
            self.session.exec(
                select(HeadlessDataSetModelConfig).where(
                    HeadlessDataSetModelConfig.oid == oid,
                    HeadlessDataSetModelConfig.dataset_id == dataset_id,
                    HeadlessDataSetModelConfig.status == 1,
                )
            )
        )
        dataset_assets = _all(
            self.session.exec(
                select(HeadlessDataSetAsset).where(
                    HeadlessDataSetAsset.oid == oid,
                    HeadlessDataSetAsset.dataset_id == dataset_id,
                    HeadlessDataSetAsset.status == 1,
                )
            )
        )
        runtime_configs = _runtime_dataset_configs(dataset, dataset_model_configs)
        configured_model_ids = [config["id"] for config in runtime_configs if config.get("id") is not None]
        if configured_model_ids:
            models = _all(
                self.session.exec(
                    select(HeadlessModel).where(
                        HeadlessModel.oid == oid,
                        HeadlessModel.id.in_(configured_model_ids),
                        HeadlessModel.status == 1,
                    )
                )
            )
            order_by_model_id = {model_id: index for index, model_id in enumerate(configured_model_ids)}
            models.sort(key=lambda model: order_by_model_id.get(model.id, len(order_by_model_id)))
        else:
            models = _all(
                self.session.exec(
                    select(HeadlessModel).where(
                        HeadlessModel.oid == oid,
                        HeadlessModel.domain_id == dataset.domain_id,
                        HeadlessModel.status == 1,
                    )
                )
            )
        model_domain_ids = _selected_model_domain_ids(models)
        subject_domains = self._load_subject_domains(oid, model_domain_ids, domain)
        datasource_ids = {model.datasource_id for model in models if model.datasource_id is not None}
        datasources = (
            _all(
                self.session.exec(
                    select(CoreDatasource).where(
                        CoreDatasource.oid == oid,
                        CoreDatasource.id.in_(datasource_ids),
                    )
                )
            )
            if datasource_ids
            else []
        )
        model_ids = [model.id for model in models if model.id is not None]
        model_fields = (
            _all(
                self.session.exec(
                    select(HeadlessModelField).where(
                        HeadlessModelField.oid == oid,
                        HeadlessModelField.model_id.in_(model_ids),
                        HeadlessModelField.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        model_measures = (
            _all(
                self.session.exec(
                    select(HeadlessModelMeasure).where(
                        HeadlessModelMeasure.oid == oid,
                        HeadlessModelMeasure.model_id.in_(model_ids),
                        HeadlessModelMeasure.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        metrics = (
            _all(
                self.session.exec(
                    select(HeadlessMetric).where(
                        HeadlessMetric.oid == oid,
                        HeadlessMetric.model_id.in_(model_ids),
                        HeadlessMetric.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        dimensions = (
            _all(
                self.session.exec(
                    select(HeadlessDimension).where(
                        HeadlessDimension.oid == oid,
                        HeadlessDimension.model_id.in_(model_ids),
                        HeadlessDimension.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        dimension_ids = [dimension.id for dimension in dimensions if dimension.id is not None]
        dimension_values = (
            _all(
                self.session.exec(
                    select(HeadlessDimensionValue).where(
                        HeadlessDimensionValue.oid == oid,
                        HeadlessDimensionValue.dimension_id.in_(dimension_ids),
                        HeadlessDimensionValue.status == 1,
                    )
                )
            )
            if dimension_ids
            else []
        )
        terms = _all(
            self.session.exec(
                select(HeadlessTerm).where(
                    HeadlessTerm.oid == oid,
                    HeadlessTerm.domain_id.in_(model_domain_ids or [dataset.domain_id]),
                    HeadlessTerm.status == 1,
                )
            )
        )
        model_relations = (
            _all(
                self.session.exec(
                    select(HeadlessModelRelation).where(
                        HeadlessModelRelation.oid == oid,
                        HeadlessModelRelation.domain_id.in_(model_domain_ids or [dataset.domain_id]),
                        HeadlessModelRelation.status == 1,
                    )
                )
            )
            if model_ids
            else []
        )
        return self.build_from_assets(
            dataset,
            domain,
            models,
            metrics,
            dimensions,
            terms,
            datasources,
            model_relations,
            model_fields=model_fields,
            model_measures=model_measures,
            dimension_values=dimension_values,
            dataset_model_configs=dataset_model_configs,
            dataset_assets=dataset_assets,
            subject_domains=subject_domains,
        )

    def _load_subject_domains(
        self,
        oid: int,
        domain_ids: list[int],
        fallback_domain: HeadlessDomain | None,
    ) -> list[HeadlessDomain]:
        domains: list[HeadlessDomain] = []
        seen: set[int] = set()
        for domain_id in domain_ids:
            if domain_id in seen:
                continue
            seen.add(domain_id)
            domain = fallback_domain if fallback_domain is not None and fallback_domain.id == domain_id else None
            if domain is None:
                domain = self.session.get(HeadlessDomain, domain_id)
            if domain is not None and domain.oid == oid and domain.status == 1:
                domains.append(domain)
        return domains

    def build_from_assets(
        self,
        dataset: HeadlessDataSet,
        domain: HeadlessDomain | None,
        models: list[HeadlessModel],
        metrics: list[HeadlessMetric],
        dimensions: list[HeadlessDimension],
        terms: list[HeadlessTerm],
        datasources: list[CoreDatasource] | None = None,
        model_relations: list[HeadlessModelRelation] | None = None,
        model_fields: list[HeadlessModelField] | None = None,
        model_measures: list[HeadlessModelMeasure] | None = None,
        dimension_values: list[HeadlessDimensionValue] | None = None,
        dataset_model_configs: list[HeadlessDataSetModelConfig] | None = None,
        dataset_assets: list[HeadlessDataSetAsset] | None = None,
        subject_domains: list[HeadlessDomain] | None = None,
    ) -> DataSetSchema:
        configs = _runtime_dataset_configs(dataset, dataset_model_configs)
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
        datasource_by_id = {datasource.id: datasource for datasource in datasources or []}
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
        schema = DataSetSchema(
            database_type=database_type,
            database_version=database_version,
            data_set=data_set_element,
            subject_domains=_runtime_subject_domains(selected_models, subject_domains or ([domain] if domain else [])),
            models=[self._model_runtime(model, fields_by_model.get(model.id, []), measures_by_model.get(model.id, [])) for model in selected_models],
            model_relations=exposed_relations,
            metrics=[self._metric_element(dataset, metric) for metric in exposed_metrics],
            dimensions=[self._dimension_element(dataset, dimension, model_by_id.get(dimension.model_id)) for dimension in exposed_dimensions],
            terms=[self._term_element(dataset, term) for term in terms if term.status == 1],
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
        model: HeadlessModel,
        fields: list[HeadlessModelField] | None = None,
        measures: list[HeadlessModelMeasure] | None = None,
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
            "measures": [_measure_with_datasource_id(item, model.id) for item in detail.get("measures") or []],
        }

    def _metric_element(self, dataset: HeadlessDataSet, metric: HeadlessMetric) -> SchemaElement:
        normalize_model_storage_fields(metric)
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
        dataset: HeadlessDataSet,
        dimension: HeadlessDimension,
        model: HeadlessModel | None,
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
        dataset: HeadlessDataSet,
        dimension: HeadlessDimension,
        model: HeadlessModel | None,
        values: list[HeadlessDimensionValue] | None = None,
    ) -> SchemaElement:
        aliases: list[str] = []
        schema_value_maps = build_dim_value_maps_from_storage(values or []) or dimension.dim_value_maps or []
        for item in schema_value_maps:
            aliases.extend(_value_aliases(item))
        element = self._dimension_element(dataset, dimension, model)
        element.type = "VALUE"
        element.alias = _unique(aliases)
        element.schema_value_maps = schema_value_maps
        return element

    def _term_element(self, dataset: HeadlessDataSet, term: HeadlessTerm) -> SchemaElement:
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


@dataclass
class HeadlessWord:
    text: str
    element: SchemaElement
    word: str
    similarity: float


class HeadlessKnowledgeService:
    def build_words(self, schema: DataSetSchema) -> list[HeadlessWord]:
        words: list[HeadlessWord] = []
        for element in [*schema.metrics, *schema.dimensions, *schema.dimension_values, *schema.terms]:
            words.extend(self._element_words(element))
        words.sort(key=lambda item: (-len(item.text), item.element.type, item.element.id))
        return words

    def _element_words(self, element: SchemaElement) -> list[HeadlessWord]:
        raw_words = _unique([element.name, element.biz_name, *(element.alias or [])])
        if element.type == "VALUE":
            raw_words = _unique(element.alias or [])
        return [HeadlessWord(text=word, element=element, word=_match_word(element, word), similarity=1.0) for word in raw_words]


class HeadlessSchemaMapper:
    def __init__(self, knowledge_service: HeadlessKnowledgeService | None = None):
        self.knowledge_service = knowledge_service or HeadlessKnowledgeService()

    def map_schema(self, query_text: str, schema: DataSetSchema) -> SchemaMapInfo:
        matches: list[SchemaElementMatch] = []
        occupied: list[range] = []
        for word in self.knowledge_service.build_words(schema):
            offset = query_text.find(word.text)
            if offset < 0:
                continue
            span = range(offset, offset + len(word.text))
            if _overlaps(span, occupied):
                continue
            occupied.append(span)
            matches.append(
                SchemaElementMatch(
                    element=word.element,
                    offset=offset,
                    similarity=word.similarity,
                    detect_word=word.text,
                    word=word.word,
                    frequency=0,
                )
            )
        matches.sort(key=lambda item: (_type_order(item.element.type), item.offset, -len(item.detect_word)))
        return SchemaMapInfo(data_set_element_matches={schema.data_set.id: matches})


def _runtime_dataset_configs(
    dataset: HeadlessDataSet,
    storage_configs: list[HeadlessDataSetModelConfig] | None = None,
) -> list[dict[str, Any]]:
    active_configs = [item for item in storage_configs or [] if item.status == 1]
    if active_configs:
        return [
            {"id": item.model_id, "includes_all": item.includes_all}
            for item in sorted(active_configs, key=lambda config: config.sort_order)
        ]
    return [{"id": item.id, "includes_all": item.includes_all} for item in _dataset_configs(dataset)]


def _selected_model_domain_ids(models: list[HeadlessModel]) -> list[int]:
    domain_ids: list[int] = []
    for model in models:
        if model.domain_id not in domain_ids:
            domain_ids.append(model.domain_id)
    return domain_ids


def _runtime_subject_domains(
    models: list[HeadlessModel],
    domains: list[HeadlessDomain | None],
) -> list[dict[str, Any]]:
    domains_by_id = {domain.id: domain for domain in domains if domain is not None and domain.id is not None}
    model_ids_by_domain: dict[int, list[int]] = {}
    for model in models:
        if model.id is None:
            continue
        model_ids_by_domain.setdefault(model.domain_id, []).append(model.id)

    result: list[dict[str, Any]] = []
    for domain_id in _selected_model_domain_ids(models):
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


def _runtime_dataset_asset_ids(
    dataset: HeadlessDataSet,
    storage_assets: list[HeadlessDataSetAsset] | None = None,
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
    configs = _dataset_configs(dataset)
    return (
        {config.id: set(config.metrics) for config in configs if not config.includes_all},
        {config.id: set(config.dimensions) for config in configs if not config.includes_all},
    )


def _group_by_model(items: list[HeadlessModelField] | list[HeadlessModelMeasure]) -> dict[int | None, list[Any]]:
    grouped: dict[int | None, list[Any]] = {}
    for item in items:
        grouped.setdefault(item.model_id, []).append(item)
    return grouped


def _group_values_by_dimension(items: list[HeadlessDimensionValue]) -> dict[int | None, list[HeadlessDimensionValue]]:
    grouped: dict[int | None, list[HeadlessDimensionValue]] = {}
    for item in items:
        if item.enabled and item.status == 1:
            grouped.setdefault(item.dimension_id, []).append(item)
    return grouped


def _model_field_type(model: HeadlessModel, field_name: str) -> str | None:
    for field in (model.model_detail or {}).get("fields", []):
        if field.get("fieldName") == field_name or field.get("field_name") == field_name:
            return field.get("dataType") or field.get("data_type")
    return None


def build_ontology_from_schema(schema: DataSetSchema) -> Ontology:
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


def _normalize_model_detail(payload: ModelCreateWithAssetsPayload) -> dict[str, Any]:
    model_detail = dict(payload.model_detail or {})
    if payload.source_type.upper() == "SQL":
        model_detail.setdefault("queryType", "sql_query")
    else:
        model_detail.setdefault("queryType", "table_query")
    if payload.table_name:
        model_detail.setdefault("tableQuery", {"table": payload.table_name})
    if payload.sql:
        model_detail.setdefault("sqlQuery", {"sql": payload.sql})
    model_detail.setdefault("fields", [])
    model_detail.setdefault("identifiers", [])
    model_detail.setdefault("dimensions", [])
    model_detail.setdefault("measures", [])
    model_detail.setdefault("sqlVariables", [])
    return model_detail


def _dimension_from_detail(item: dict[str, Any], oid: int, model_id: int) -> HeadlessDimension:
    dimension_type = item.get("type") or "categorical"
    return HeadlessDimension(
        oid=oid,
        model_id=model_id,
        name=item.get("name") or item.get("bizName") or item.get("biz_name"),
        biz_name=item.get("bizName") or item.get("biz_name"),
        alias=item.get("alias") or [],
        type=dimension_type,
        semantic_type=item.get("semanticType") or item.get("semantic_type"),
        expr=item.get("expr") or item.get("bizName") or item.get("biz_name"),
        data_type=item.get("dataType") or item.get("data_type"),
        field_name=item.get("fieldName") or item.get("field_name") or item.get("bizName") or item.get("biz_name"),
        is_primary_key=dimension_type == "primary_key",
        is_default_time=_is_time_dimension_type(dimension_type),
        time_granularities=[(item.get("typeParams") or {}).get("timeGranularity")]
        if isinstance(item.get("typeParams"), dict) and (item.get("typeParams") or {}).get("timeGranularity")
        else [],
        description=item.get("description"),
        type_params=item.get("typeParams") or item.get("type_params") or {},
    )


def _metric_from_detail(item: dict[str, Any], oid: int, model_id: int) -> HeadlessMetric:
    return HeadlessMetric(
        oid=oid,
        model_id=model_id,
        name=item.get("name") or item.get("bizName") or item.get("biz_name"),
        biz_name=item.get("bizName") or item.get("biz_name"),
        alias=item.get("alias") or [],
        default_agg=item.get("agg") or item.get("defaultAgg") or item.get("default_agg") or "SUM",
        type_params={"expr": item.get("expr") or item.get("bizName") or item.get("biz_name")},
        description=item.get("description"),
    )


def _metric_from_measure_detail(item: dict[str, Any], oid: int, model_id: int) -> HeadlessMetric:
    biz_name = _measure_biz_name(item)
    default_agg = item.get("agg") or item.get("defaultAgg") or item.get("default_agg") or "SUM"
    expr = item.get("expr") or biz_name
    measure_id = item.get("id")
    # 按 Supersonic Headless 的指标定义结构记录“由模型度量定义指标”的来源。
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
    return HeadlessMetric(
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


def _measure_detail_from_storage(measure: HeadlessModelMeasure) -> dict[str, Any]:
    return {
        "id": measure.id,
        "name": measure.name,
        "bizName": measure.biz_name,
        "expr": measure.expr,
        "agg": measure.agg,
        "alias": measure.alias or [],
        "description": measure.description,
    }


def _runtime_table_query(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("table") or raw.get("tableName") or raw.get("table_name") or "").strip()
    return str(raw or "").strip()


def _runtime_sql_query(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("sql") or raw.get("query") or "").strip().rstrip(";")
    return str(raw or "").strip().rstrip(";")


def _runtime_database_info(
    models: list[HeadlessModel],
    datasource_by_id: dict[int, CoreDatasource],
) -> tuple[str | None, str | None]:
    for model in models:
        datasource = datasource_by_id.get(model.datasource_id)
        if datasource is None:
            continue
        database_type = datasource.type or datasource.type_name
        return database_type, _datasource_database_version(datasource)
    return None, None


def _datasource_database_version(datasource: CoreDatasource) -> str | None:
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
    relations: list[HeadlessModelRelation],
    model_by_id: dict[int | None, HeadlessModel],
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


def _metric_type_params_for_runtime(metric: HeadlessMetric) -> dict[str, Any]:
    params = dict(metric.type_params or {})
    measure_params = params.get("metricDefineByMeasureParams")
    if isinstance(measure_params, dict):
        measure_params = dict(measure_params)
        measure_params["measures"] = [
            _measure_with_datasource_id(item, metric.model_id)
            for item in measure_params.get("measures", [])
            if isinstance(item, dict)
        ]
        params["metricDefineByMeasureParams"] = measure_params
    return params


def _metric_fields(metric: HeadlessMetric) -> list[str]:
    params = dict(metric.type_params or {})
    define_type = params.get("metricDefineType") or metric.define_type or "MEASURE"
    if define_type == "FIELD":
        field_params = params.get("metricDefineByFieldParams") or {}
        return _unique(
            [
                field.get("fieldName") or field.get("field_name") or field.get("bizName") or field.get("biz_name")
                for field in field_params.get("fields", [])
                if isinstance(field, dict)
            ]
            or [field_params.get("expr") or metric.biz_name]
        )
    if define_type == "METRIC":
        metric_params = params.get("metricDefineByMetricParams") or {}
        return _unique(
            [
                item.get("bizName") or item.get("biz_name") or item.get("name")
                for item in metric_params.get("metrics", [])
                if isinstance(item, dict)
            ]
            or [metric_params.get("expr") or metric.biz_name]
        )
    measure_params = params.get("metricDefineByMeasureParams") or {}
    measures = measure_params.get("measures", []) if isinstance(measure_params, dict) else []
    return _unique(
        [
            item.get("expr") or item.get("bizName") or item.get("biz_name")
            for item in measures
            if isinstance(item, dict)
        ]
        or [metric.biz_name]
    )


def _measure_with_datasource_id(item: dict[str, Any], model_id: int | None) -> dict[str, Any]:
    measure = dict(item)
    if model_id is not None and not measure.get("datasourceId"):
        measure["datasourceId"] = model_id
    return measure


def _dimension_for_runtime(item: dict[str, Any]) -> dict[str, Any]:
    dimension = dict(item)
    dimension_type = dimension.get("type") or "categorical"
    if _is_time_dimension_type(dimension_type):
        dimension.setdefault("dateFormat", "yyyy-MM-dd")
        dimension.setdefault("typeParams", {"isPrimary": "true", "timeGranularity": "day"})
    return dimension


def _measure_biz_name(item: dict[str, Any]) -> str:
    return str(item.get("bizName") or item.get("biz_name") or "").strip()


def _infer_field_role(field_name: str, data_type: str | None) -> str:
    normalized = field_name.lower()
    if normalized == "id" or normalized.endswith("_id"):
        return "IDENTIFIER"
    if _is_time_type(data_type) or any(token in normalized for token in ["date", "time", "day", "month"]):
        return "DIMENSION"
    if _is_numeric_type(data_type):
        return "MEASURE"
    return "DIMENSION"


def _display_name(comment: str | None, field_name: str) -> str:
    text = str(comment or "").strip()
    return text or field_name


def _is_numeric_type(data_type: str | None) -> bool:
    lowered = str(data_type or "").lower()
    return any(token in lowered for token in ["int", "decimal", "double", "float", "numeric", "number", "real"])


def _is_time_type(data_type: str | None) -> bool:
    lowered = str(data_type or "").lower()
    return any(token in lowered for token in ["date", "time", "timestamp", "datetime"])


def _value_aliases(item: dict[str, Any]) -> list[str]:
    return _unique([item.get("bizName"), *(item.get("alias") or []), item.get("biz_name")])


def _match_word(element: SchemaElement, text: str) -> str:
    if element.type != "VALUE":
        return element.name
    for value_map in element.schema_value_maps:
        if text in _value_aliases(value_map) or text == str(value_map.get("value") or ""):
            return str(value_map.get("value") or value_map.get("techName") or value_map.get("tech_name") or text)
    return text


def _overlaps(span: range, occupied: list[range]) -> bool:
    return any(span.start < item.stop and item.start < span.stop for item in occupied)


def _type_order(element_type: str) -> int:
    return {"METRIC": 0, "DIMENSION": 1, "VALUE": 2, "TERM": 3}.get(element_type, 9)


def _all(result: Any) -> list[Any]:
    if hasattr(result, "scalars"):
        return result.scalars().all()
    if hasattr(result, "all"):
        return result.all()
    return list(result or [])
