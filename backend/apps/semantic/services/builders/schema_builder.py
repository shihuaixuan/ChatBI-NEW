from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any, Literal, cast

from apps.datasource import DatasourceRecord
from apps.semantic.models.dto import (
    DatasetCalendarContract,
    DatasetSchema,
    DimensionHierarchyRuntimeDTO,
    DimensionHierarchyRuntimeLevelDTO,
    JoinRelation,
    MetricRelationshipRuntimeDTO,
    Ontology,
    SchemaElement,
)
from apps.semantic.models.orm import (
    BusinessEntity,
    DimensionHierarchy,
    DimensionHierarchyLevel,
    LogicalDimension,
    MetricDimensionCapability,
    MetricRelationship,
    MetricRelationshipDimension,
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetInstruction,
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
from apps.semantic.services.relation_path import relation_path_error
from apps.semantic.utils.orm_mapping import (
    build_dim_value_maps_from_storage,
    build_model_detail_from_storage,
    normalize_model_source,
)
from apps.semantic.utils.schema_selection import (
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
            business_entities=assets.business_entities,
            logical_dimensions=assets.logical_dimensions,
            metric_dimension_capabilities=assets.metric_dimension_capabilities,
            dimension_hierarchies=assets.dimension_hierarchies,
            dimension_hierarchy_levels=assets.dimension_hierarchy_levels,
            metric_relationships=assets.metric_relationships,
            metric_relationship_dimensions=assets.metric_relationship_dimensions,
            instructions=assets.instructions,
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
        business_entities: list[BusinessEntity] | None = None,
        logical_dimensions: list[LogicalDimension] | None = None,
        metric_dimension_capabilities: list[MetricDimensionCapability] | None = None,
        dimension_hierarchies: list[DimensionHierarchy] | None = None,
        dimension_hierarchy_levels: list[DimensionHierarchyLevel] | None = None,
        metric_relationships: list[MetricRelationship] | None = None,
        metric_relationship_dimensions: list[MetricRelationshipDimension] | None = None,
        instructions: list[SemanticDatasetInstruction] | None = None,
    ) -> DatasetSchema:
        configs = runtime_dataset_configs(dataset, dataset_model_configs)
        selected_model_ids = {config["id"] for config in configs}
        # 发布快照只暴露已认证资产；构建待发布草稿时允许先组装完整候选。
        published_only = dataset.contract_version > 0
        selected_models = [
            model
            for model in models
            if model.id in selected_model_ids and model.status == 1
        ]
        if published_only:
            selected_models = [
                model for model in selected_models if model.contract_status == "READY"
            ]
        model_by_id = {model.id: model for model in selected_models}
        metric_ids_by_model, dimension_ids_by_model = _runtime_dataset_asset_ids(
            dataset, dataset_assets
        )
        includes_all_models = {
            config["id"] for config in configs if config["includes_all"]
        }

        exposed_metrics = [
            metric
            for metric in metrics
            if metric.model_id in model_by_id
            and metric.status == 1
            and metric.quality_status != "INVALID"
            and (not published_only or metric.contract_version is not None)
            and (
                metric.model_id in includes_all_models
                or metric.id in metric_ids_by_model.get(metric.model_id, set())
            )
        ]
        exposed_dimensions = [
            dimension
            for dimension in dimensions
            if dimension.model_id in model_by_id
            and dimension.status == 1
            and (not published_only or dimension.contract_version is not None)
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
        database_type, database_version = _runtime_database_info(
            selected_models, datasource_by_id
        )
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
        hierarchy_runtime = _dimension_hierarchies_runtime(
            dimension_hierarchies or [],
            dimension_hierarchy_levels or [],
            exposed_dimensions,
            metric_dimension_capabilities or [],
        )
        relationship_runtime = _metric_relationships_runtime(
            metric_relationships or [],
            metric_relationship_dimensions or [],
            exposed_metrics,
            exposed_dimensions,
            metric_dimension_capabilities or [],
            model_relations or [],
        )
        schema = DatasetSchema(
            database_type=database_type,
            database_version=database_version,
            data_set=data_set_element,
            calendar=DatasetCalendarContract(
                default_timezone=dataset.default_timezone,
                calendar_type=cast(
                    Literal["NATURAL", "FISCAL", "BUSINESS"],
                    dataset.calendar_type,
                ),
                week_start_day=dataset.week_start_day,
                fiscal_year_start_month=dataset.fiscal_year_start_month,
                holiday_calendar_key=dataset.holiday_calendar_key,
            ),
            subject_domains=_runtime_subject_domains(
                selected_models, subject_domains or ([domain] if domain else [])
            ),
            models=[
                self._model_runtime(
                    model,
                    fields_by_model.get(model.id, []),
                    measures_by_model.get(model.id, []),
                )
                for model in selected_models
            ],
            model_relations=exposed_relations,
            metrics=[
                self._metric_element(dataset, metric) for metric in exposed_metrics
            ],
            dimensions=[
                self._dimension_element(
                    dataset, dimension, model_by_id.get(dimension.model_id)
                )
                for dimension in exposed_dimensions
            ],
            terms=[
                self._term_element(dataset, term)
                for term in terms
                if term.status == 1 and _term_applies_to_dataset(term, dataset)
            ],
            query_config=_runtime_query_config(dataset.query_config or {}),
            business_entities=[
                _business_entity_runtime(item) for item in business_entities or []
            ],
            logical_dimensions=[
                _logical_dimension_runtime(item) for item in logical_dimensions or []
            ],
            metric_dimension_capabilities=[
                _metric_dimension_capability_runtime(item)
                for item in metric_dimension_capabilities or []
            ],
            dimension_hierarchies=hierarchy_runtime,
            research_relationships=relationship_runtime,
            model_contracts=[_model_contract_runtime(item) for item in selected_models],
            relation_contracts=[
                _relation_contract_runtime(item) for item in model_relations or []
            ],
            metric_contracts=[
                _metric_contract_runtime(item) for item in exposed_metrics
            ],
            instructions=_instruction_runtime(instructions or []),
            schema_version=dataset.schema_version,
            contract_version=_dataset_contract_version(
                selected_models,
                exposed_metrics,
                exposed_dimensions,
                model_relations or [],
                business_entities or [],
                logical_dimensions or [],
                metric_dimension_capabilities or [],
                dimension_hierarchies or [],
                dimension_hierarchy_levels or [],
                metric_relationships or [],
                metric_relationship_dimensions or [],
            ),
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
        schema.asset_versions = _asset_versions(
            selected_models,
            exposed_metrics,
            exposed_dimensions,
            model_relations or [],
            business_entities or [],
            logical_dimensions or [],
            metric_dimension_capabilities or [],
            dimension_hierarchies or [],
            dimension_hierarchy_levels or [],
            metric_relationships or [],
            metric_relationship_dimensions or [],
        )
        schema.asset_versions[f"dataset:{dataset.id}"] = dataset.contract_version or 0
        schema.contract_version = max(
            schema.contract_version, dataset.contract_version or 0
        )
        schema.schema_fingerprint = _schema_fingerprint(schema)
        return schema

    def _model_runtime(
        self,
        model: SemanticModel,
        fields: list[SemanticModelField] | None = None,
        measures: list[SemanticModelMeasure] | None = None,
    ) -> dict[str, Any]:
        normalize_model_source(model)
        if fields or measures:
            detail = build_model_detail_from_storage(
                model, fields or [], measures or []
            )
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
            "queryType": detail.get("queryType")
            or ("sql_query" if model.source_type.upper() == "SQL" else "table_query"),
            "tableQuery": table_query,
            "sqlQuery": sql_query,
            "filterSql": model.filter_sql or "",
            "depends": model.depends or [],
            "fields": detail.get("fields") or [],
            "identifiers": detail.get("identifiers") or [],
            "dimensions": [
                _dimension_for_runtime(item) for item in detail.get("dimensions") or []
            ],
            "measures": [
                measure_with_datasource_id(item, model.id)
                for item in detail.get("measures") or []
            ],
        }

    def _metric_element(
        self, dataset: SemanticDataset, metric: SemanticMetric
    ) -> SchemaElement:
        normalize_metric_storage_fields(metric)
        # 检索投影必须在统一入口识别敏感资产，不能依赖下游猜测。
        ext_info = {**(metric.ext or {}), "sensitive_level": metric.sensitive_level}
        ext_info["metric_refs"] = _metric_formula_refs(metric)
        if metric.formula_definition:
            ext_info["formula_definition"] = dict(metric.formula_definition)
        ext_info["time_semantics"] = metric.time_semantics
        ext_info["snapshot_aggregation"] = metric.snapshot_aggregation
        ext_info["comparison_grains"] = list(metric.comparison_grains or [])
        ext_info["time_alignment_policy"] = metric.time_alignment_policy
        # 指标级过滤口径随运行时 schema 下发，编译器据此合并进 WHERE。
        if metric.filter_sql:
            ext_info["filter_sql"] = metric.filter_sql
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
            ext_info["dimension_data_type"] = _model_field_type(
                model, dimension.biz_name
            )
        if dimension.logical_dimension_id is not None:
            ext_info["logical_dimension_id"] = dimension.logical_dimension_id
        if dimension.contract_version is not None:
            ext_info["contract_version"] = dimension.contract_version
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
        schema_value_maps = (
            build_dim_value_maps_from_storage(values or [])
            or dimension.dim_value_maps
            or []
        )
        for item in schema_value_maps:
            aliases.extend(value_aliases(item))
        element = self._dimension_element(dataset, dimension, model)
        element.type = "VALUE"
        element.alias = unique_texts(aliases)
        element.schema_value_maps = schema_value_maps
        return element

    def _term_element(
        self, dataset: SemanticDataset, term: SemanticTerm
    ) -> SchemaElement:
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
                *[
                    {"type": "METRIC", "id": metric_id}
                    for metric_id in term.related_metrics or []
                ],
                *[
                    {"type": "DIMENSION", "id": dimension_id}
                    for dimension_id in term.related_dimensions or []
                ],
            ],
        )


def _runtime_subject_domains(
    models: list[SemanticModel],
    domains: Sequence[SemanticDomain | None],
) -> list[dict[str, Any]]:
    domains_by_id = {
        domain.id: domain
        for domain in domains
        if domain is not None and domain.id is not None
    }
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


def _business_entity_runtime(entity: BusinessEntity) -> dict[str, Any]:
    return {
        "id": entity.id,
        "domain_id": entity.domain_id,
        "name": entity.name,
        "biz_name": entity.biz_name,
        "description": entity.description,
        "key_type": entity.key_type,
        "value_domain_key": entity.value_domain_key,
        "version": entity.version,
    }


def _logical_dimension_runtime(dimension: LogicalDimension) -> dict[str, Any]:
    return {
        "id": dimension.id,
        "domain_id": dimension.domain_id,
        "entity_id": dimension.entity_id,
        "name": dimension.name,
        "biz_name": dimension.biz_name,
        "description": dimension.description,
        "semantic_type": dimension.semantic_type,
        "value_type": dimension.value_type,
        "value_domain_key": dimension.value_domain_key,
        "version": dimension.version,
    }


def _metric_dimension_capability_runtime(
    capability: MetricDimensionCapability,
) -> dict[str, Any]:
    return {
        "id": capability.id,
        "metric_id": capability.metric_id,
        "logical_dimension_id": capability.logical_dimension_id,
        "usages": capability.usages,
        "binding_strategy": capability.binding_strategy,
        "relation_path": capability.relation_path,
        "target_model_id": capability.target_model_id,
        "physical_dimension_id": capability.physical_dimension_id,
        "aggregation_safety": capability.aggregation_safety,
        "pre_aggregation_grain": capability.pre_aggregation_grain,
        "time_alignment_policy": capability.time_alignment_policy,
        "contribution_tolerance": capability.contribution_tolerance,
        "version": capability.version,
    }


def _model_contract_runtime(model: SemanticModel) -> dict[str, Any]:
    return {
        "model_id": model.id,
        "model_kind": model.model_kind,
        "row_description": model.row_description,
        "model_grain": model.model_grain,
        "primary_key": model.primary_key,
        "event_time_field": model.event_time_field,
        "snapshot_time_field": model.snapshot_time_field,
        "contract_status": model.contract_status,
        "contract_version": model.contract_version or 0,
    }


def _relation_contract_runtime(relation: SemanticModelRelation) -> dict[str, Any]:
    return {
        "relation_id": relation.id,
        "left_model_id": relation.left_model_id,
        "right_model_id": relation.right_model_id,
        "cardinality": relation.cardinality,
        "left_unique": relation.left_unique,
        "right_unique": relation.right_unique,
        "metric_propagation": relation.metric_propagation,
        "aggregation_safety": relation.aggregation_safety,
        "valid_time_condition": relation.valid_time_condition,
        "contract_status": relation.contract_status,
        "contract_version": relation.contract_version or 0,
    }


def _metric_contract_runtime(metric: SemanticMetric) -> dict[str, Any]:
    return {
        "metric_id": metric.id,
        "model_id": metric.model_id,
        "default_agg": metric.default_agg,
        "result_grain": metric.result_grain,
        "additivity": metric.additivity,
        "distinct_keys": metric.distinct_keys,
        "time_semantics": metric.time_semantics,
        "default_time_dimension_id": metric.default_time_dimension_id,
        "snapshot_aggregation": metric.snapshot_aggregation,
        "comparison_grains": list(metric.comparison_grains or []),
        "time_alignment_policy": metric.time_alignment_policy,
        "contract_version": metric.contract_version or metric.version,
        "formula_definition": metric.formula_definition or {},
    }


def _metric_formula_refs(metric: SemanticMetric) -> list[int]:
    """从结构化公式投影兼容引用，旧指标继续读取 metric_refs。"""

    components = (metric.formula_definition or {}).get("components")
    if isinstance(components, list):
        return [
            item["metric_id"]
            for item in components
            if isinstance(item, dict) and isinstance(item.get("metric_id"), int)
        ]
    return list(metric.metric_refs or [])


def _dimension_hierarchies_runtime(
    hierarchies: list[DimensionHierarchy],
    levels: list[DimensionHierarchyLevel],
    dimensions: list[SemanticDimension],
    _capabilities: list[MetricDimensionCapability],
) -> list[DimensionHierarchyRuntimeDTO]:
    """把已选层级解析为当前数据集可执行的物理维度绑定。"""

    levels_by_hierarchy: dict[int, list[DimensionHierarchyLevel]] = {}
    for level in levels:
        levels_by_hierarchy.setdefault(level.hierarchy_id, []).append(level)
    physical_by_logical_model: dict[tuple[int, int], list[int]] = {}
    for dimension in dimensions:
        if dimension.id is None or dimension.logical_dimension_id is None:
            continue
        physical_by_logical_model.setdefault(
            (dimension.logical_dimension_id, dimension.model_id), []
        ).append(dimension.id)
    result: list[DimensionHierarchyRuntimeDTO] = []
    for hierarchy in hierarchies:
        if hierarchy.id is None or hierarchy.contract_status != "CERTIFIED":
            continue
        hierarchy_levels = sorted(
            levels_by_hierarchy.get(hierarchy.id, []),
            key=lambda item: item.level_order,
        )
        if len(hierarchy_levels) < 2:
            continue
        runtime_levels = tuple(
            DimensionHierarchyRuntimeLevelDTO(
                logical_dimension_id=item.logical_dimension_id,
                level_order=item.level_order,
                physical_dimension_ids=tuple(
                    sorted(
                        {
                            physical_id
                            for (
                                logical_id,
                                _model_id,
                            ), physical_ids in physical_by_logical_model.items()
                            if logical_id == item.logical_dimension_id
                            for physical_id in physical_ids
                        }
                    )
                ),
            )
            for item in hierarchy_levels
        )
        refs_by_model: dict[str, tuple[str, ...]] = {}
        model_ids = {dimension.model_id for dimension in dimensions}
        for model_id in sorted(model_ids):
            physical_refs: list[str] = []
            for level in hierarchy_levels:
                physical_ids = physical_by_logical_model.get(
                    (level.logical_dimension_id, model_id), []
                )
                if len(physical_ids) != 1:
                    break
                physical_refs.append(f"DIMENSION:{physical_ids[0]}:{model_id}")
            else:
                refs_by_model[str(model_id)] = tuple(physical_refs)
        if not refs_by_model:
            continue
        result.append(
            DimensionHierarchyRuntimeDTO(
                id=hierarchy.id,
                domain_id=hierarchy.domain_id,
                hierarchy_type="FIXED_LEVEL",
                contract_status="CERTIFIED",
                version=hierarchy.version,
                levels=runtime_levels,
                dimension_refs_by_model=refs_by_model,
            )
        )
    return result


def _metric_relationships_runtime(
    relationships: list[MetricRelationship],
    relationship_dimensions: list[MetricRelationshipDimension],
    metrics: list[SemanticMetric],
    dimensions: list[SemanticDimension],
    capabilities: list[MetricDimensionCapability],
    model_relations: list[SemanticModelRelation],
) -> list[MetricRelationshipRuntimeDTO]:
    """将已选指标关系解析为 Research 可直接使用的严格 DTO。"""

    dimensions_by_relationship: dict[int, list[int]] = {}
    for item in relationship_dimensions:
        dimensions_by_relationship.setdefault(item.relationship_id, []).append(
            item.logical_dimension_id
        )
    metric_by_id = {item.id: item for item in metrics if item.id is not None}
    dimension_by_id = {item.id: item for item in dimensions if item.id is not None}
    physical_by_metric_logical: dict[tuple[int, int], list[int]] = {}
    for capability in capabilities:
        if capability.physical_dimension_id is None:
            continue
        physical = dimension_by_id.get(capability.physical_dimension_id)
        if physical is None or physical.id is None:
            continue
        usages = {str(item).upper() for item in capability.usages or []}
        if "GROUP_BY" not in usages or capability.aggregation_safety == "FORBIDDEN":
            continue
        physical_by_metric_logical.setdefault(
            (capability.metric_id, capability.logical_dimension_id), []
        ).append(physical.id)
    relation_by_id = {
        item.id: item
        for item in model_relations
        if item.id is not None and item.status == 1
    }
    result: list[MetricRelationshipRuntimeDTO] = []
    for relationship in relationships:
        if relationship.id is None or relationship.contract_status != "CERTIFIED":
            continue
        # 结构化公式统一在下方投影为包含全部组成指标的一条关系。
        if relationship.relationship_type == "FORMULA_COMPONENT":
            continue
        target = metric_by_id.get(relationship.target_metric_id)
        driver = metric_by_id.get(relationship.driver_metric_id)
        if target is None or driver is None:
            continue
        if target.id is None or driver.id is None:
            continue
        target_id = target.id
        driver_id = driver.id
        is_cross_model = target.model_id != driver.model_id
        if is_cross_model and relationship.relationship_type not in {
            "CERTIFIED_DRIVER",
            "GOVERNED_ANALYSIS_RELATION",
        }:
            continue
        relation_path = tuple(relationship.relation_path or [])
        if is_cross_model and not _valid_certified_relation_path(
            relation_path,
            target.model_id,
            driver.model_id,
            relation_by_id,
        ):
            continue
        if not _compatible_metric_time_contracts(target, driver):
            continue
        logical_ids = tuple(
            dict.fromkeys(dimensions_by_relationship.get(relationship.id, []))
        )
        dimension_refs: list[str] = []
        dimension_refs_by_model: dict[str, tuple[str, ...]] = {}
        for logical_id in logical_ids:
            target_physical = physical_by_metric_logical.get(
                (target_id, logical_id), []
            )
            driver_physical = physical_by_metric_logical.get(
                (driver_id, logical_id), []
            )
            if len(target_physical) != 1 or len(driver_physical) != 1:
                break
            if not is_cross_model and target_physical != driver_physical:
                break
            target_ref = f"DIMENSION:{target_physical[0]}:{target.model_id}"
            driver_ref = f"DIMENSION:{driver_physical[0]}:{driver.model_id}"
            dimension_refs.extend(
                [target_ref, driver_ref] if is_cross_model else [target_ref]
            )
        else:
            if is_cross_model and not logical_ids:
                continue
            target_dimension_refs = tuple(
                ref for ref in dimension_refs if ref.endswith(f":{target.model_id}")
            )
            driver_dimension_refs = tuple(
                ref for ref in dimension_refs if ref.endswith(f":{driver.model_id}")
            )
            if is_cross_model:
                dimension_refs_by_model = {
                    str(target.model_id): target_dimension_refs,
                    str(driver.model_id): driver_dimension_refs,
                }
            time_roles = tuple(
                dict.fromkeys(relationship.supported_time_roles or ["single"])
            )
            target_ref = f"METRIC:{target_id}:{target.model_id}"
            driver_ref = f"METRIC:{driver_id}:{driver.model_id}"
            fingerprint = _schema_asset_fingerprint(
                {
                    "id": relationship.id,
                    "version": relationship.version,
                    "target": target_ref,
                    "driver": driver_ref,
                    "type": relationship.relationship_type.lower(),
                    "validation_method": relationship.validation_method,
                    "direction": relationship.expected_direction,
                    "dimensions": dimension_refs,
                    "relation_path": relation_path,
                    "time_roles": time_roles,
                }
            )
            result.append(
                MetricRelationshipRuntimeDTO(
                    id=str(relationship.id),
                    target_metric_ref=target_ref,
                    driver_metric_ref=driver_ref,
                    relationship_type=cast(
                        Literal[
                            "formula_component",
                            "certified_driver",
                            "governed_analysis_relation",
                        ],
                        relationship.relationship_type.lower(),
                    ),
                    validation_method=cast(
                        Literal[
                            "SAME_DIRECTION",
                            "OPPOSITE_DIRECTION",
                            "FORMULA_RECONCILIATION",
                        ],
                        relationship.validation_method,
                    ),
                    expected_direction=cast(
                        Literal["POSITIVE", "NEGATIVE", "UNKNOWN"],
                        relationship.expected_direction,
                    ),
                    dimension_refs=tuple(dict.fromkeys(dimension_refs)),
                    dimension_refs_by_model=dimension_refs_by_model,
                    relation_path=relation_path,
                    time_roles=time_roles,
                    relationship_fingerprint=fingerprint,
                )
            )
    # 公式对账必须把目标指标和全部组成指标冻结为一个整体关系。
    for target in metrics:
        if target.id is None:
            continue
        target_id = target.id
        formula = target.formula_definition or {}
        components = formula.get("components")
        if not isinstance(components, list):
            continue
        component_ids = tuple(
            dict.fromkeys(
                component["metric_id"]
                for component in components
                if isinstance(component, dict)
                and isinstance(component.get("metric_id"), int)
            )
        )
        drivers = tuple(metric_by_id.get(metric_id) for metric_id in component_ids)
        if not component_ids or any(
            driver is None or driver.model_id != target.model_id for driver in drivers
        ):
            continue
        target_ref = f"METRIC:{target_id}:{target.model_id}"
        component_metric_refs = tuple(
            f"METRIC:{metric_id}:{target.model_id}" for metric_id in component_ids
        )
        formula_dimension_refs = tuple(
            f"DIMENSION:{target_physical[0]}:{target.model_id}"
            for logical_id in sorted(
                {
                    logical_id
                    for metric_id, logical_id in physical_by_metric_logical
                    if metric_id == target_id
                }
            )
            if len(
                target_physical := physical_by_metric_logical.get(
                    (target_id, logical_id), []
                )
            )
            == 1
            and all(
                target_physical
                == physical_by_metric_logical.get((metric_id, logical_id), [])
                for metric_id in component_ids
            )
        )
        time_roles = ("single",)
        relationship_id = "formula:" + ":".join(
            str(metric_id) for metric_id in (target_id, *component_ids)
        )
        fingerprint = _schema_asset_fingerprint(
            {
                "id": relationship_id,
                "target": target_ref,
                "components": component_metric_refs,
                "type": "formula_component",
                "validation_method": "FORMULA_RECONCILIATION",
                "formula": formula,
                "dimensions": formula_dimension_refs,
                "time_roles": time_roles,
            }
        )
        result.append(
            MetricRelationshipRuntimeDTO(
                id=relationship_id,
                target_metric_ref=target_ref,
                driver_metric_ref=component_metric_refs[0],
                component_metric_refs=component_metric_refs,
                relationship_type="formula_component",
                validation_method="FORMULA_RECONCILIATION",
                expected_direction="UNKNOWN",
                dimension_refs=formula_dimension_refs,
                time_roles=time_roles,
                relationship_fingerprint=fingerprint,
            )
        )
    return result


def _valid_certified_relation_path(
    relation_path: tuple[int, ...],
    source_model_id: int,
    target_model_id: int,
    relations: dict[int, SemanticModelRelation],
) -> bool:
    """校验跨模型关系路径连续、已发布且不会直接引入不安全聚合。"""

    return (
        relation_path_error(
            relation_path,
            source_model_id,
            target_model_id,
            relations,
            allowed_contract_statuses={"READY"},
        )
        is None
    )


def _compatible_metric_time_contracts(
    target: SemanticMetric,
    driver: SemanticMetric,
) -> bool:
    """跨模型驱动关系必须具备可比较的时间语义。"""

    if (
        target.time_alignment_policy != "NONE"
        and driver.time_alignment_policy != "NONE"
        and target.time_alignment_policy != driver.time_alignment_policy
    ):
        return False
    target_grains = set(target.comparison_grains or [])
    driver_grains = set(driver.comparison_grains or [])
    return not target_grains or not driver_grains or bool(target_grains & driver_grains)


def _instruction_runtime(
    instructions: list[SemanticDatasetInstruction],
) -> dict[str, list[str]]:
    """按模块投影启用指令，并按版本升序保持稳定顺序。"""

    grouped: dict[str, list[SemanticDatasetInstruction]] = {}
    for instruction in instructions:
        if not instruction.enabled:
            continue
        grouped.setdefault(instruction.module, []).append(instruction)
    return {
        module: [
            item.content for item in sorted(items, key=lambda value: value.version)
        ]
        for module, items in grouped.items()
    }


def _dataset_contract_version(
    models: list[SemanticModel],
    metrics: list[SemanticMetric],
    dimensions: list[SemanticDimension],
    relations: list[SemanticModelRelation],
    entities: list[BusinessEntity],
    logical_dimensions: list[LogicalDimension],
    capabilities: list[MetricDimensionCapability],
    hierarchies: list[DimensionHierarchy],
    _hierarchy_levels: list[DimensionHierarchyLevel],
    metric_relationships: list[MetricRelationship],
    _relationship_dimensions: list[MetricRelationshipDimension],
) -> int:
    versions = [
        *(item.contract_version or 0 for item in models),
        *(item.contract_version or item.version for item in metrics),
        *(item.contract_version or 0 for item in dimensions),
        *(item.contract_version or 0 for item in relations),
        *(item.version for item in entities),
        *(item.version for item in logical_dimensions),
        *(item.version for item in capabilities),
        *(item.version for item in hierarchies),
        *(item.version for item in metric_relationships),
    ]
    return max(versions, default=0)


def _asset_versions(
    models: list[SemanticModel],
    metrics: list[SemanticMetric],
    dimensions: list[SemanticDimension],
    relations: list[SemanticModelRelation],
    entities: list[BusinessEntity],
    logical_dimensions: list[LogicalDimension],
    capabilities: list[MetricDimensionCapability],
    hierarchies: list[DimensionHierarchy],
    hierarchy_levels: list[DimensionHierarchyLevel],
    metric_relationships: list[MetricRelationship],
    relationship_dimensions: list[MetricRelationshipDimension],
) -> dict[str, int]:
    return {
        **{f"model:{item.id}": item.contract_version or 0 for item in models},
        **{
            f"metric:{item.id}": item.contract_version or item.version
            for item in metrics
        },
        **{f"dimension:{item.id}": item.contract_version or 0 for item in dimensions},
        **{f"relation:{item.id}": item.contract_version or 0 for item in relations},
        **{f"business_entity:{item.id}": item.version for item in entities},
        **{f"logical_dimension:{item.id}": item.version for item in logical_dimensions},
        **{f"capability:{item.id}": item.version for item in capabilities},
        **{f"dimension_hierarchy:{item.id}": item.version for item in hierarchies},
        **{f"dimension_hierarchy_level:{item.id}": 1 for item in hierarchy_levels},
        **{
            f"metric_relationship:{item.id}": item.version
            for item in metric_relationships
        },
        **{
            f"metric_relationship_dimension:{item.id}": 1
            for item in relationship_dimensions
        },
    }


def _schema_fingerprint(schema: DatasetSchema) -> str:
    payload = schema.model_dump(mode="json", exclude={"schema_fingerprint"})
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _schema_asset_fingerprint(payload: dict[str, Any]) -> str:
    """为运行时关系生成稳定指纹。"""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _runtime_query_config(query_config: dict[str, Any]) -> dict[str, Any]:
    """只保留仍属于数据集运行策略的配置，移除旧治理事实源。"""

    return {
        key: value
        for key, value in query_config.items()
        if key not in {"dimension_hierarchies", "research_relationships", "semanticEnforcement"}
    }


def _term_applies_to_dataset(
    term: SemanticTerm,
    dataset: SemanticDataset,
) -> bool:
    """未指定数据集的术语作用于整个主题域，否则只作用于明确的数据集。"""

    return not term.related_datasets or dataset.id in term.related_datasets


def _runtime_dataset_asset_ids(
    _dataset: SemanticDataset,
    storage_assets: list[SemanticDatasetAsset] | None = None,
) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    active_assets = [item for item in storage_assets or [] if item.status == 1]
    if active_assets:
        metric_ids_by_model: dict[int, set[int]] = {}
        dimension_ids_by_model: dict[int, set[int]] = {}
        for asset in active_assets:
            if asset.model_id is None:
                continue
            if asset.asset_type == "METRIC":
                metric_ids_by_model.setdefault(asset.model_id, set()).add(
                    asset.asset_id
                )
            elif asset.asset_type == "DIMENSION":
                dimension_ids_by_model.setdefault(asset.model_id, set()).add(
                    asset.asset_id
                )
        return metric_ids_by_model, dimension_ids_by_model
    return {}, {}


def _group_by_model(
    items: list[SemanticModelField] | list[SemanticModelMeasure],
) -> dict[int | None, list[Any]]:
    grouped: dict[int | None, list[Any]] = {}
    for item in items:
        grouped.setdefault(item.model_id, []).append(item)
    return grouped


def _group_values_by_dimension(
    items: list[SemanticDimensionValue],
) -> dict[int | None, list[SemanticDimensionValue]]:
    grouped: dict[int | None, list[SemanticDimensionValue]] = {}
    for item in items:
        if item.enabled and item.status == 1:
            grouped.setdefault(item.dimension_id, []).append(item)
    return grouped


def _model_field_type(model: SemanticModel, field_name: str) -> str | None:
    for field in (model.model_detail or {}).get("fields", []):
        if (
            field.get("fieldName") == field_name
            or field.get("field_name") == field_name
        ):
            value = field.get("dataType") or field.get("data_type")
            return str(value) if value is not None else None
    return None


def build_ontology_from_schema(schema: DatasetSchema) -> Ontology:
    model_map = {
        str(model.get("biz_name") or model.get("name")): model
        for model in schema.models
    }
    model_name_by_id = {
        model.get("id"): str(model.get("biz_name") or model.get("name"))
        for model in schema.models
    }
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
        return str(
            raw.get("table") or raw.get("tableName") or raw.get("table_name") or ""
        ).strip()
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
    for key in [
        "databaseVersion",
        "database_version",
        "dbVersion",
        "db_version",
        "version",
    ]:
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
                join_condition=[
                    _runtime_join_condition(item)
                    for item in relation.join_conditions or []
                ],
            )
        )
    return runtime_relations


def _runtime_join_condition(item: dict[str, Any]) -> list[str]:
    left_field = item.get("leftField") or item.get("left_field") or item.get("left")
    operator = item.get("operator") or item.get("op") or "="
    right_field = item.get("rightField") or item.get("right_field") or item.get("right")
    return [
        str(left_field or "").strip(),
        str(operator or "=").strip(),
        str(right_field or "").strip(),
    ]


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
    if metric.formula_definition:
        params["formula_definition"] = dict(metric.formula_definition)
    return params


def _metric_fields(metric: SemanticMetric) -> list[str]:
    params = dict(metric.type_params or {})
    define_type = params.get("metricDefineType") or metric.define_type or "MEASURE"
    if define_type == "FIELD":
        field_params = params.get("metricDefineByFieldParams") or {}
        return unique_texts(
            [
                field.get("fieldName")
                or field.get("field_name")
                or field.get("bizName")
                or field.get("biz_name")
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
    measures = (
        measure_params.get("measures", []) if isinstance(measure_params, dict) else []
    )
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
        dimension.setdefault(
            "typeParams", {"isPrimary": "true", "timeGranularity": "day"}
        )
    return dimension
