"""Semantic 语义资产到统一检索资源的安全投影。"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from apps.retrieval.models.dto import RetrievalResourceType, RetrievalSourceType
from apps.retrieval.projection.contracts import (
    ProjectedResource,
    ProjectedUnit,
    projection_content_hash,
)
from apps.retrieval.projection.contracts import (
    ProjectedResourceDelta as ProjectedResourceDelta,
)
from apps.semantic.models.dto import DatasetSchema, JoinRelation, SchemaElement


@dataclass(frozen=True, slots=True)
class SemanticProjectionPolicy:
    """维值接入策略；配置来自 retrieval_source，而不是查询时临时判断。"""

    configured_value_dimension_ids: frozenset[int] = frozenset()
    max_configured_values_per_dimension: int = 200

    def __post_init__(self) -> None:
        if any(dimension_id <= 0 for dimension_id in self.configured_value_dimension_ids):
            raise ValueError("维值投影配置中的维度 ID 必须为正整数")
        if self.max_configured_values_per_dimension <= 0:
            raise ValueError("维值投影的最大基数必须大于 0")


class SemanticSourceProjector:
    """把 Semantic 业务资产投影为细粒度、安全且可增量更新的检索单元。"""

    def __init__(self, policy: SemanticProjectionPolicy | None = None) -> None:
        self.policy = policy or SemanticProjectionPolicy()

    def project(
        self,
        schema: DatasetSchema,
        *,
        tenant_id: int,
        namespace: str,
        source_version: str,
        acl: dict[str, Any] | None = None,
        visibility: Literal["private", "tenant", "public"] = "tenant",
        permission_version: str | None = None,
    ) -> list[ProjectedResource]:
        """生成稳定顺序的 Semantic 检索资源，不执行数据库写入或 embedding。"""

        if tenant_id <= 0:
            raise ValueError("tenant_id 必须为正整数")
        if not namespace.strip():
            raise ValueError("namespace 不能为空")
        if not source_version.strip():
            raise ValueError("source_version 不能为空")

        context = _ProjectionContext(
            tenant_id=tenant_id,
            namespace=namespace,
            source_version=source_version,
            acl=acl or {},
            visibility=visibility,
            permission_version=permission_version,
        )
        relationships = _RelationshipIndex(schema)
        resources: list[ProjectedResource] = [self._project_dataset(schema, context)]
        for model in schema.models:
            projected_model = self._project_model(schema, model, context)
            if projected_model is not None:
                resources.append(projected_model)
        for metric in schema.metrics:
            if not _is_sensitive(metric):
                resources.append(self._project_metric(schema, metric, context, relationships))
        for dimension in schema.dimensions:
            if not _is_sensitive(dimension):
                resources.append(self._project_dimension(schema, dimension, context, relationships))
        for term in schema.terms:
            if not _is_sensitive(term):
                resources.append(self._project_term(schema, term, context))
        for value_dictionary in schema.dimension_values:
            if not _is_sensitive(value_dictionary):
                resource = self._project_values(schema, value_dictionary, context)
                if resource is not None:
                    resources.append(resource)
        return sorted(resources, key=lambda item: (item.resource_type.value, item.source_resource_id))

    def _project_dataset(
        self,
        schema: DatasetSchema,
        context: _ProjectionContext,
    ) -> ProjectedResource:
        dataset = schema.data_set
        aliases = _normalized_texts(dataset.alias)
        domains = _subject_domains(schema)
        model_ids = sorted(
            model_id
            for model in schema.models
            if isinstance((model_id := model.get("id")), int) and model_id > 0
        )
        common_metadata = {
            "asset_type": "DATASET",
            "asset_id": dataset.id,
            "dataset_id": dataset.data_set_id,
            "biz_name": dataset.biz_name,
            "subject_domain_ids": [domain["domain_id"] for domain in domains],
            "model_ids": model_ids,
        }
        units = [
            ProjectedUnit.create(
                unit_key="identity",
                content_kind="identity",
                title=dataset.name,
                content=_lines(
                    ("数据集名称", dataset.name),
                    ("数据集别名", "、".join(aliases)),
                ),
                metadata={**common_metadata, "aliases": aliases},
            ),
            ProjectedUnit.create(
                unit_key="definition",
                content_kind="definition",
                title=f"{dataset.name}定义",
                content=_lines(("数据集定义", dataset.description or dataset.name)),
                metadata=common_metadata,
            ),
        ]
        for domain in domains:
            domain_metadata = {
                **common_metadata,
                "domain_id": domain["domain_id"],
                "domain_biz_name": domain["biz_name"],
                "domain_model_ids": domain["model_ids"],
            }
            units.append(
                ProjectedUnit.create(
                    unit_key=f"subject-domain:{domain['domain_id']}",
                    content_kind="subject_domain",
                    title=domain["name"],
                    content=_lines(
                        ("主题域名称", domain["name"]),
                        ("主题域定义", domain["description"] or domain["name"]),
                    ),
                    contextual_text=_dataset_context(schema),
                    metadata=domain_metadata,
                )
            )
        return _resource(
            context=context,
            resource_type=RetrievalResourceType.DATASET,
            element=dataset,
            title=dataset.name,
            metadata=common_metadata,
            units=tuple(units),
        )

    def _project_model(
        self,
        schema: DatasetSchema,
        model: dict[str, Any],
        context: _ProjectionContext,
    ) -> ProjectedResource | None:
        model_id = model.get("id")
        if not isinstance(model_id, int) or model_id <= 0:
            return None
        name = _safe_string(model.get("name")) or _safe_string(model.get("biz_name"))
        biz_name = _safe_string(model.get("biz_name")) or name
        if name is None or biz_name is None:
            return None

        domains = [domain for domain in _subject_domains(schema) if model_id in domain["model_ids"]]
        domain_names = [domain["name"] for domain in domains]
        safe_metrics = [metric for metric in schema.metrics if metric.model == model_id and not _is_sensitive(metric)]
        safe_dimensions = [
            dimension
            for dimension in schema.dimensions
            if dimension.model == model_id and not _is_sensitive(dimension)
        ]
        metric_ids = sorted(metric.id for metric in safe_metrics)
        dimension_ids = sorted(dimension.id for dimension in safe_dimensions)
        metric_names = _normalized_texts(metric.name for metric in safe_metrics)
        dimension_names = _normalized_texts(
            dimension.name for dimension in safe_dimensions
        )
        common_metadata = {
            "asset_type": "MODEL",
            "asset_id": model_id,
            "dataset_id": schema.data_set.id,
            "biz_name": biz_name,
            "subject_domain_ids": [domain["domain_id"] for domain in domains],
            "related_metric_ids": metric_ids,
            "related_dimension_ids": dimension_ids,
        }
        element = SchemaElement(
            data_set_id=schema.data_set.id,
            data_set_name=schema.data_set.name,
            model=model_id,
            id=model_id,
            name=name,
            biz_name=biz_name,
            type="MODEL",
        )
        identity = ProjectedUnit.create(
            unit_key="identity",
            content_kind="identity",
            title=name,
            content=_lines(("数据模型名称", name)),
            contextual_text=_dataset_context(schema),
            metadata=common_metadata,
        )
        scope = ProjectedUnit.create(
            unit_key="scope",
            content_kind="scope",
            title=f"{name}分析范围",
            content=_lines(
                ("所属主题域", "、".join(domain_names)),
                ("可用指标", "、".join(metric_names)),
                ("可用维度", "、".join(dimension_names)),
            ),
            contextual_text=_dataset_context(schema),
            metadata=common_metadata,
        )
        return _resource(
            context=context,
            resource_type=RetrievalResourceType.MODEL,
            element=element,
            title=name,
            metadata=common_metadata,
            units=(identity, scope),
        )

    def _project_metric(
        self,
        schema: DatasetSchema,
        metric: SchemaElement,
        context: _ProjectionContext,
        relationships: _RelationshipIndex,
    ) -> ProjectedResource:
        aliases = _normalized_texts(metric.alias)
        relationship_metadata = relationships.for_model(metric.model, schema.dimensions)
        common_metadata = _asset_metadata(metric)
        resource_metadata = {**common_metadata, **relationship_metadata}
        name = ProjectedUnit.create(
            unit_key="name",
            content_kind="name",
            title=metric.name,
            content=metric.name,
            metadata={**common_metadata, **relationship_metadata},
        )
        units = [name]
        if aliases:
            alias_text = ",".join(aliases)
            units.append(
                ProjectedUnit.create(
                    unit_key="aliases",
                    content_kind="aliases",
                    title=f"{metric.name}别名",
                    content=alias_text,
                    metadata={**common_metadata, "aliases": aliases},
                )
            )
        if metric.biz_name and metric.biz_name != metric.name:
            units.append(
                ProjectedUnit.create(
                    unit_key="biz_name",
                    content_kind="biz_name",
                    title=f"{metric.name}业务名",
                    content=metric.biz_name,
                    metadata=common_metadata,
                )
            )
        return _resource(
            context=context,
            resource_type=RetrievalResourceType.METRIC,
            element=metric,
            title=metric.name,
            metadata=resource_metadata,
            units=tuple(units),
        )

    def _project_dimension(
        self,
        schema: DatasetSchema,
        dimension: SchemaElement,
        context: _ProjectionContext,
        relationships: _RelationshipIndex,
    ) -> ProjectedResource:
        aliases = _normalized_texts(dimension.alias)
        relationship_metadata = relationships.for_model(dimension.model, schema.dimensions)
        common_metadata = _asset_metadata(dimension)
        resource_metadata = {**common_metadata, **relationship_metadata}
        identity = ProjectedUnit.create(
            unit_key="identity",
            content_kind="identity",
            title=dimension.name,
            content=_lines(
                ("维度名称", dimension.name),
                ("维度别名", "、".join(aliases)),
            ),
            contextual_text=_dataset_context(schema),
            metadata={**common_metadata, "aliases": aliases},
        )
        definition = ProjectedUnit.create(
            unit_key="definition",
            content_kind="definition",
            title=f"{dimension.name}定义",
            content=_lines(("维度定义", dimension.description or dimension.name)),
            contextual_text=_dataset_context(schema),
            metadata=common_metadata,
        )
        role_metadata = {
            **common_metadata,
            "dimension_type": _safe_string(dimension.ext_info.get("dimension_type")),
            "semantic_type": _safe_string(dimension.ext_info.get("semantic_type")),
            "is_primary_key": bool(dimension.ext_info.get("is_primary_key")),
            "is_default_time": bool(dimension.ext_info.get("is_default_time")),
            "time_granularities": _normalized_texts(dimension.ext_info.get("time_granularities") or []),
            **relationship_metadata,
        }
        role = ProjectedUnit.create(
            unit_key="role",
            content_kind="role",
            title=f"{dimension.name}角色",
            content=_lines(
                ("维度类型", role_metadata["dimension_type"] or "未指定"),
                ("语义类型", role_metadata["semantic_type"]),
                ("业务角色", _dimension_role(role_metadata)),
            ),
            contextual_text=_dataset_context(schema),
            metadata=role_metadata,
        )
        return _resource(
            context=context,
            resource_type=RetrievalResourceType.DIMENSION,
            element=dimension,
            title=dimension.name,
            metadata=resource_metadata,
            units=(identity, definition, role),
        )

    def _project_term(
        self,
        schema: DatasetSchema,
        term: SchemaElement,
        context: _ProjectionContext,
    ) -> ProjectedResource:
        aliases = _normalized_texts(term.alias)
        common_metadata = _asset_metadata(term)
        definition = ProjectedUnit.create(
            unit_key="definition",
            content_kind="definition",
            title=term.name,
            content=_lines(
                ("术语名称", term.name),
                ("术语别名", "、".join(aliases)),
                ("术语定义", term.description or term.name),
            ),
            contextual_text=_dataset_context(schema),
            metadata={**common_metadata, "aliases": aliases},
        )
        metric_ids = _related_asset_ids(term, "METRIC")
        dimension_ids = _related_asset_ids(term, "DIMENSION")
        relation_types = [
            label
            for label, ids in [("指标", metric_ids), ("维度", dimension_ids)]
            if ids
        ]
        relationships = ProjectedUnit.create(
            unit_key="relationships",
            content_kind="relationships",
            title=f"{term.name}关联资产",
            content=_lines(("关联资产类型", "、".join(relation_types) or "暂无已治理关系")),
            contextual_text=_dataset_context(schema),
            metadata={
                **common_metadata,
                "related_metric_ids": metric_ids,
                "related_dimension_ids": dimension_ids,
            },
        )
        return _resource(
            context=context,
            resource_type=RetrievalResourceType.TERM,
            element=term,
            title=term.name,
            metadata=common_metadata,
            units=(definition, relationships),
        )

    def _project_values(
        self,
        schema: DatasetSchema,
        value_dictionary: SchemaElement,
        context: _ProjectionContext,
    ) -> ProjectedResource | None:
        value_maps = value_dictionary.schema_value_maps or []
        configured = (
            value_dictionary.id in self.policy.configured_value_dimension_ids
            and len(value_maps) <= self.policy.max_configured_values_per_dimension
        )
        units: list[ProjectedUnit] = []
        for value_map in value_maps:
            governed = _is_governed_value(value_map)
            common = bool(value_map.get("isCommon") or value_map.get("is_common") or value_map.get("common"))
            if not (governed or common or configured):
                continue
            display_value = _display_value(value_map)
            if not display_value:
                continue
            aliases = _normalized_texts(value_map.get("alias") or [])
            aliases = [alias for alias in aliases if alias != display_value]
            canonical_value = _canonical_value(value_map) or display_value
            unit_key = f"value:{hashlib.sha256(canonical_value.encode('utf-8')).hexdigest()[:24]}"
            units.append(
                ProjectedUnit.create(
                    unit_key=unit_key,
                    content_kind="value",
                    title=display_value,
                    content=_lines(
                        ("所属维度", value_dictionary.name),
                        ("维值名称", display_value),
                        ("维值别名", "、".join(aliases)),
                    ),
                    contextual_text=_dataset_context(schema),
                    metadata={
                        "asset_type": "VALUE",
                        "dimension_id": value_dictionary.id,
                        "model_id": value_dictionary.model,
                        "canonical_value": canonical_value,
                        "aliases": aliases,
                        "governed": governed,
                        "common": common,
                    },
                )
            )
        if not units:
            return None

        common_metadata = {
            "asset_type": "VALUE",
            "asset_id": value_dictionary.id,
            "dimension_id": value_dictionary.id,
            "model_id": value_dictionary.model,
            "dataset_id": value_dictionary.data_set_id,
        }
        return _resource(
            context=context,
            resource_type=RetrievalResourceType.VALUE,
            element=value_dictionary,
            title=f"{value_dictionary.name}维值",
            metadata=common_metadata,
            units=tuple(sorted(units, key=lambda item: item.unit_key)),
        )


@dataclass(frozen=True, slots=True)
class _ProjectionContext:
    tenant_id: int
    namespace: str
    source_version: str
    acl: dict[str, Any]
    visibility: Literal["private", "tenant", "public"]
    permission_version: str | None


class _RelationshipIndex:
    """仅保存结构化模型关系，主动丢弃 join condition。"""

    def __init__(self, schema: DatasetSchema) -> None:
        self._model_id_by_name = {
            str(model.get("biz_name") or model.get("name")): model.get("id")
            for model in schema.models
            if isinstance(model.get("id"), int)
        }
        self._relations: list[dict[str, Any]] = []
        for relation in schema.model_relations:
            safe_relation = self._safe_relation(relation)
            if safe_relation is not None:
                self._relations.append(safe_relation)

    def _safe_relation(self, relation: JoinRelation) -> dict[str, Any] | None:
        left_model_id = self._model_id_by_name.get(relation.left)
        right_model_id = self._model_id_by_name.get(relation.right)
        if not isinstance(left_model_id, int) or not isinstance(right_model_id, int):
            return None
        return {
            "relation_id": relation.id,
            "left_model_id": left_model_id,
            "right_model_id": right_model_id,
            "join_type": relation.join_type,
        }

    def for_model(self, model_id: int | None, dimensions: list[SchemaElement]) -> dict[str, Any]:
        if model_id is None:
            return {
                "same_model_dimension_ids": [],
                "joinable_model_ids": [],
                "compatible_dimension_ids": [],
                "model_relations": [],
            }
        model_relations = [
            relation
            for relation in self._relations
            if model_id in {relation["left_model_id"], relation["right_model_id"]}
        ]
        joinable_model_ids = sorted(
            {
                relation["right_model_id"]
                if relation["left_model_id"] == model_id
                else relation["left_model_id"]
                for relation in model_relations
            }
        )
        same_model_dimension_ids = sorted(dimension.id for dimension in dimensions if dimension.model == model_id)
        compatible_model_ids = {model_id, *joinable_model_ids}
        compatible_dimension_ids = sorted(
            dimension.id for dimension in dimensions if dimension.model in compatible_model_ids
        )
        return {
            "same_model_dimension_ids": same_model_dimension_ids,
            "joinable_model_ids": joinable_model_ids,
            "compatible_dimension_ids": compatible_dimension_ids,
            "model_relations": sorted(
                model_relations,
                key=lambda item: (
                    item["left_model_id"],
                    item["right_model_id"],
                    item["relation_id"] or 0,
                ),
            ),
        }


def _resource(
    *,
    context: _ProjectionContext,
    resource_type: RetrievalResourceType,
    element: SchemaElement,
    title: str,
    metadata: dict[str, Any],
    units: tuple[ProjectedUnit, ...],
) -> ProjectedResource:
    source_resource_id = f"{resource_type.value}:{element.id}"
    ordered_units = tuple(sorted(units, key=lambda item: item.unit_key))
    content_hash = projection_content_hash(
        {
            "resource_type": resource_type.value,
            "source_resource_id": source_resource_id,
            "dataset_id": element.data_set_id,
            "title": title,
            "metadata": metadata,
            "acl": context.acl,
            "visibility": context.visibility,
            "permission_version": context.permission_version,
            "units": [
                {"unit_key": unit.unit_key, "content_hash": unit.content_hash}
                for unit in ordered_units
            ],
        }
    )
    return ProjectedResource(
        tenant_id=context.tenant_id,
        namespace=context.namespace,
        resource_type=resource_type,
        source_type=RetrievalSourceType.SEMANTIC,
        source_resource_id=source_resource_id,
        dataset_id=element.data_set_id,
        title=title,
        metadata=metadata,
        acl=context.acl,
        visibility=context.visibility,
        permission_version=context.permission_version,
        source_version=context.source_version,
        content_hash=content_hash,
        units=ordered_units,
    )


def _asset_metadata(element: SchemaElement) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "asset_type": element.type,
        "asset_id": element.id,
        "dataset_id": element.data_set_id,
        "biz_name": element.biz_name,
    }
    if element.model is not None and element.model > 0:
        metadata["model_id"] = element.model
    if element.is_tag:
        metadata["is_tag"] = True
    return metadata


def _normalized_texts(items: Iterable[Any]) -> list[str]:
    values = {str(item).strip() for item in items if item is not None and str(item).strip()}
    return sorted(values, key=lambda value: (value.casefold(), value))


def _lines(*items: tuple[str, Any]) -> str:
    return "\n".join(f"{label}：{value}" for label, value in items if value not in {None, ""})


def _dataset_context(schema: DatasetSchema) -> str:
    return f"数据集：{schema.data_set.name}"


def _subject_domains(schema: DatasetSchema) -> list[dict[str, Any]]:
    domains: list[dict[str, Any]] = []
    for item in schema.subject_domains:
        domain_id = item.get("domain_id")
        name = _safe_string(item.get("name") or item.get("domain_name"))
        if not isinstance(domain_id, int) or domain_id <= 0 or name is None:
            continue
        raw_model_ids = item.get("model_ids") or []
        model_ids = sorted(
            model_id
            for model_id in raw_model_ids
            if isinstance(model_id, int) and model_id > 0
        )
        domains.append(
            {
                "domain_id": domain_id,
                "name": name,
                "biz_name": _safe_string(item.get("biz_name") or item.get("domain_biz_name")) or name,
                "description": _safe_string(item.get("description")),
                "model_ids": model_ids,
            }
        )
    return sorted(domains, key=lambda item: item["domain_id"])


def _safe_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _metric_define_type(metric: SchemaElement) -> str:
    value = metric.type_params.get("metricDefineType") or metric.type_params.get("metric_define_type")
    return str(value or "业务指标")


def _dimension_role(metadata: dict[str, Any]) -> str:
    roles: list[str] = []
    if metadata["is_primary_key"]:
        roles.append("主键维度")
    if metadata["is_default_time"]:
        roles.append("默认时间维度")
    return "、".join(roles) or "分析维度"


def _related_asset_ids(element: SchemaElement, asset_type: str) -> list[int]:
    return sorted(
        {
            int(item["id"])
            for item in element.related_schema_elements
            if str(item.get("type") or "").upper() == asset_type
            and isinstance(item.get("id"), int)
        }
    )


def _is_sensitive(element: SchemaElement) -> bool:
    if element.ext_info.get("sensitive") is True:
        return True
    sensitive_level = element.ext_info.get("sensitive_level", element.ext_info.get("sensitiveLevel", 0))
    return isinstance(sensitive_level, (int, float)) and sensitive_level > 0


def _is_governed_value(value_map: dict[str, Any]) -> bool:
    if _normalized_texts(value_map.get("alias") or []):
        return True
    if value_map.get("governed") is True or value_map.get("isGoverned") is True:
        return True
    source_type = str(value_map.get("sourceType") or value_map.get("source_type") or "").upper()
    return source_type in {"MANUAL", "CURATED", "GOVERNED"}


def _display_value(value_map: dict[str, Any]) -> str:
    value = (
        value_map.get("displayValue")
        or value_map.get("display_value")
        or value_map.get("bizName")
        or value_map.get("biz_name")
    )
    if value is None:
        aliases = _normalized_texts(value_map.get("alias") or [])
        value = aliases[0] if aliases else ""
    return str(value).strip()


def _canonical_value(value_map: dict[str, Any]) -> str:
    value = value_map.get("value") or value_map.get("techName") or value_map.get("tech_name")
    return str(value or "").strip()
