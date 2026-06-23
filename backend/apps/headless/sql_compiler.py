from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from apps.headless.schemas import DataSetSchema, JoinRelation, SchemaElement
from apps.headless.service import build_ontology_from_schema


@dataclass
class SemanticSQLCompileRequest:
    schema: DataSetSchema
    question: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    metric_ids: list[int] = field(default_factory=list)
    dimension_ids: list[int] = field(default_factory=list)
    repair_context: dict[str, Any] = field(default_factory=dict)
    limit: int | None = None


@dataclass
class SemanticSQLCompileResult:
    sql: str
    tables: list[str]
    metrics: list[str]
    dimensions: list[str]


class SemanticSQLCompiler:
    def compile(self, request: SemanticSQLCompileRequest) -> SemanticSQLCompileResult:
        ontology = build_ontology_from_schema(request.schema)
        metrics = self._select_metrics(request)
        dimensions = self._select_dimensions(request)
        filters = self._select_filters(request)
        model_by_name = ontology.model_map
        model_name_by_id = {model.get("id"): name for name, model in model_by_name.items()}
        metrics = self._repair_elements_by_candidate_tables(
            metrics,
            request.schema.metrics,
            request.repair_context,
            model_by_name,
            model_name_by_id,
        )
        dimensions = self._repair_elements_by_candidate_tables(
            dimensions,
            request.schema.dimensions,
            request.repair_context,
            model_by_name,
            model_name_by_id,
        )
        filters = [
            (dimension, operator, value)
            for dimension, operator, value in (
                (self._repair_element_by_candidate_tables(dimension, request.schema.dimensions, request.repair_context, model_by_name, model_name_by_id), operator, value)
                for dimension, operator, value in filters
            )
            if dimension is not None
        ]
        filter_dimensions = [item[0] for item in filters]
        if not metrics and not dimensions and not filter_dimensions:
            raise ValueError("SEMANTIC_SQL_ASSET_REQUIRED")

        selected_model_names = self._selected_model_names(metrics, [*dimensions, *filter_dimensions], model_name_by_id)
        if not selected_model_names:
            raise ValueError("SEMANTIC_SQL_MODEL_REQUIRED")

        base_model_name = self._base_model_name(metrics, dimensions, model_name_by_id)
        ordered_model_names = self._order_models(base_model_name, selected_model_names, ontology.join_relations)
        model_sql = {name: self._model_source(model_by_name[name], alias=name) for name in ordered_model_names}

        from_sql = self._build_from_sql(ordered_model_names, model_sql, ontology.join_relations)
        select_parts = [
            f"{self._qualified_dimension_expr(dimension, model_by_name, model_name_by_id)} as {dimension.biz_name}"
            for dimension in dimensions
        ]
        select_parts.extend(
            f"{self._metric_expr(metric, model_by_name, model_name_by_id)} as {metric.biz_name}"
            for metric in metrics
        )
        where_parts = [
            *self._model_filters(ordered_model_names, model_by_name),
            *self._slot_filter_conditions(filters, model_by_name, model_name_by_id),
        ]
        group_parts = [self._qualified_dimension_expr(dimension, model_by_name, model_name_by_id) for dimension in dimensions]

        sql = f"select {', '.join(select_parts)} from {from_sql}"
        if where_parts:
            sql += " where " + " and ".join(where_parts)
        if metrics and group_parts:
            sql += " group by " + ", ".join(group_parts)
        if request.limit:
            sql += f" limit {request.limit}"

        return SemanticSQLCompileResult(
            sql=sql,
            tables=[self._model_table(model_by_name[name]) for name in ordered_model_names if self._model_table(model_by_name[name])],
            metrics=[metric.biz_name for metric in metrics],
            dimensions=[dimension.biz_name for dimension in dimensions],
        )

    def _select_metrics(self, request: SemanticSQLCompileRequest) -> list[SchemaElement]:
        metric_ids = set(request.metric_ids or self._slot_asset_ids(request.slots, "metrics", "METRIC"))
        if metric_ids:
            return [metric for metric in request.schema.metrics if metric.id in metric_ids]
        return self._match_elements(request.question, request.schema.metrics)

    def _select_dimensions(self, request: SemanticSQLCompileRequest) -> list[SchemaElement]:
        dimension_ids = set(request.dimension_ids or self._slot_asset_ids(request.slots, "dimensions", "DIMENSION"))
        if dimension_ids:
            return [dimension for dimension in request.schema.dimensions if dimension.id in dimension_ids]
        return self._match_elements(request.question, request.schema.dimensions)

    def _select_filters(self, request: SemanticSQLCompileRequest) -> list[tuple[SchemaElement, str, Any]]:
        dimension_by_id = {dimension.id: dimension for dimension in request.schema.dimensions}
        filters: list[tuple[SchemaElement, str, Any]] = []
        for item in request.slots.get("filters") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("asset_type") or "").upper() not in {"", "DIMENSION"}:
                continue
            dimension = dimension_by_id.get(item.get("asset_id"))
            if dimension is None:
                continue
            filters.append((dimension, item.get("operator") or "=", item.get("value")))
        return filters

    def _repair_elements_by_candidate_tables(
        self,
        selected_elements: list[SchemaElement],
        all_elements: list[SchemaElement],
        repair_context: dict[str, Any],
        model_by_name: dict[str, dict[str, Any]],
        model_name_by_id: dict[int | None, str],
    ) -> list[SchemaElement]:
        return [
            repaired
            for repaired in (
                self._repair_element_by_candidate_tables(
                    element,
                    all_elements,
                    repair_context,
                    model_by_name,
                    model_name_by_id,
                )
                for element in selected_elements
            )
            if repaired is not None
        ]

    def _repair_element_by_candidate_tables(
        self,
        element: SchemaElement,
        all_elements: list[SchemaElement],
        repair_context: dict[str, Any],
        model_by_name: dict[str, dict[str, Any]],
        model_name_by_id: dict[int | None, str],
    ) -> SchemaElement | None:
        """重试时把落在失败表上的资产切到候选表上的同名资产。"""

        candidate_tables = self._candidate_tables(repair_context)
        if not candidate_tables:
            return element
        model_name = model_name_by_id.get(element.model)
        if not model_name:
            return element
        current_table = self._model_table(model_by_name.get(model_name, {}))
        if current_table in candidate_tables:
            return element
        for candidate in all_elements:
            candidate_model_name = model_name_by_id.get(candidate.model)
            if not candidate_model_name:
                continue
            candidate_table = self._model_table(model_by_name.get(candidate_model_name, {}))
            if candidate_table not in candidate_tables:
                continue
            if self._same_business_element(element, candidate):
                return candidate
        return element

    @staticmethod
    def _candidate_tables(repair_context: dict[str, Any]) -> set[str]:
        values = repair_context.get("candidate_tables") if isinstance(repair_context, dict) else []
        if not isinstance(values, list):
            return set()
        return {str(value).strip() for value in values if str(value or "").strip()}

    @staticmethod
    def _same_business_element(left: SchemaElement, right: SchemaElement) -> bool:
        return bool(left.biz_name and left.biz_name == right.biz_name) or bool(left.name and left.name == right.name)

    @staticmethod
    def _slot_asset_ids(slots: dict[str, Any], slot_name: str, asset_type: str) -> list[int]:
        values = slots.get(slot_name) or slots.get(slot_name.rstrip("s")) or []
        if isinstance(values, dict):
            values = [values]
        result: list[int] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            if str(item.get("asset_type") or "").upper() not in {"", asset_type}:
                continue
            asset_id = item.get("asset_id")
            if isinstance(asset_id, int):
                result.append(asset_id)
        return result

    @staticmethod
    def _match_elements(question: str, elements: list[SchemaElement]) -> list[SchemaElement]:
        result: list[SchemaElement] = []
        for element in elements:
            words = [element.name, element.biz_name, *(element.alias or [])]
            if any(word and word in question for word in words):
                result.append(element)
        return result

    @staticmethod
    def _selected_model_names(
        metrics: list[SchemaElement],
        dimensions: list[SchemaElement],
        model_name_by_id: dict[int | None, str],
    ) -> list[str]:
        names: list[str] = []
        for element in [*metrics, *dimensions]:
            model_name = model_name_by_id.get(element.model)
            if model_name and model_name not in names:
                names.append(model_name)
        return names

    @staticmethod
    def _base_model_name(
        metrics: list[SchemaElement],
        dimensions: list[SchemaElement],
        model_name_by_id: dict[int | None, str],
    ) -> str:
        for metric in metrics:
            model_name = model_name_by_id.get(metric.model)
            if model_name:
                return model_name
        for dimension in dimensions:
            model_name = model_name_by_id.get(dimension.model)
            if model_name:
                return model_name
        raise ValueError("SEMANTIC_SQL_MODEL_REQUIRED")

    def _order_models(self, base_model_name: str, selected_model_names: list[str], relations: list[JoinRelation]) -> list[str]:
        ordered = [base_model_name]
        pending = [name for name in selected_model_names if name != base_model_name]
        while pending:
            next_model = None
            for candidate in pending:
                if self._find_relation(ordered, candidate, relations):
                    next_model = candidate
                    break
            if next_model is None:
                raise ValueError("SEMANTIC_SQL_JOIN_RELATION_REQUIRED")
            ordered.append(next_model)
            pending.remove(next_model)
        return ordered

    @staticmethod
    def _find_relation(existing_models: list[str], candidate: str, relations: list[JoinRelation]) -> JoinRelation | None:
        for relation in relations:
            if relation.left in existing_models and relation.right == candidate:
                return relation
            if relation.right in existing_models and relation.left == candidate:
                return relation
        return None

    def _build_from_sql(self, ordered_model_names: list[str], model_sql: dict[str, str], relations: list[JoinRelation]) -> str:
        sql = model_sql[ordered_model_names[0]]
        joined = [ordered_model_names[0]]
        for model_name in ordered_model_names[1:]:
            relation = self._find_relation(joined, model_name, relations)
            if relation is None:
                raise ValueError("SEMANTIC_SQL_JOIN_RELATION_REQUIRED")
            left_alias, right_alias = relation.left, relation.right
            if relation.right in joined and relation.left == model_name:
                left_alias, right_alias = relation.right, relation.left
            conditions = [
                f"{left_alias}.{left_field} {operator} {right_alias}.{right_field}"
                for left_field, operator, right_field in relation.join_condition
            ]
            sql += f" {relation.join_type} {model_sql[model_name]} on {' and '.join(conditions)}"
            joined.append(model_name)
        return sql

    @staticmethod
    def _model_source(model: dict[str, Any], alias: str) -> str:
        table_query = str(model.get("tableQuery") or "").strip()
        sql_query = str(model.get("sqlQuery") or "").strip()
        if table_query:
            return f"{table_query} {alias}"
        if sql_query:
            return f"({sql_query}) {alias}"
        raise ValueError("SEMANTIC_SQL_MODEL_SOURCE_REQUIRED")

    @staticmethod
    def _model_table(model: dict[str, Any]) -> str:
        return str(model.get("tableQuery") or "").strip()

    def _qualified_dimension_expr(
        self,
        dimension: SchemaElement,
        model_by_name: dict[str, dict[str, Any]],
        model_name_by_id: dict[int | None, str],
    ) -> str:
        model_name = model_name_by_id.get(dimension.model)
        if not model_name:
            raise ValueError("SEMANTIC_SQL_DIMENSION_MODEL_REQUIRED")
        expr = self._dimension_expr(model_by_name[model_name], dimension.biz_name)
        return self._qualify_expr(expr, model_name)

    @staticmethod
    def _dimension_expr(model: dict[str, Any], biz_name: str) -> str:
        for dimension in model.get("dimensions") or []:
            if (dimension.get("bizName") or dimension.get("biz_name")) == biz_name:
                return dimension.get("expr") or biz_name
        return biz_name

    def _metric_expr(
        self,
        metric: SchemaElement,
        model_by_name: dict[str, dict[str, Any]],
        model_name_by_id: dict[int | None, str],
    ) -> str:
        model_name = model_name_by_id.get(metric.model)
        if not model_name:
            raise ValueError("SEMANTIC_SQL_METRIC_MODEL_REQUIRED")
        expr, agg = self._metric_measure_expr(metric, model_by_name[model_name])
        qualified = self._qualify_expr(expr, model_name)
        if self._contains_aggregate(expr):
            return qualified
        return self._aggregate_expr(agg or metric.default_agg or "SUM", qualified)

    @staticmethod
    def _metric_measure_expr(metric: SchemaElement, model: dict[str, Any]) -> tuple[str, str | None]:
        params = metric.type_params or {}
        measure_params = params.get("metricDefineByMeasureParams") or {}
        measures = measure_params.get("measures") if isinstance(measure_params, dict) else []
        if measures:
            measure = measures[0]
            return measure.get("expr") or measure.get("bizName") or metric.biz_name, measure.get("agg")
        for measure in model.get("measures") or []:
            if (measure.get("bizName") or measure.get("biz_name")) == metric.biz_name:
                return measure.get("expr") or metric.biz_name, measure.get("agg")
        return metric.biz_name, metric.default_agg

    @staticmethod
    def _contains_aggregate(expr: str) -> bool:
        return re.search(r"\b(sum|count|avg|min|max)\s*\(", expr, flags=re.IGNORECASE) is not None

    @staticmethod
    def _aggregate_expr(agg: str, expr: str) -> str:
        normalized = agg.upper()
        if normalized == "COUNT_DISTINCT":
            return f"count(distinct {expr})"
        if normalized in {"SUM", "COUNT", "AVG", "MIN", "MAX"}:
            return f"{normalized.lower()}({expr})"
        return expr

    @staticmethod
    def _qualify_expr(expr: str, alias: str) -> str:
        expr = str(expr or "").strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr):
            return f"{alias}.{expr}"
        return expr

    @staticmethod
    def _model_filters(ordered_model_names: list[str], model_by_name: dict[str, dict[str, Any]]) -> list[str]:
        filters: list[str] = []
        for model_name in ordered_model_names:
            filter_sql = str(model_by_name[model_name].get("filterSql") or "").strip()
            if filter_sql:
                filters.append(SemanticSQLCompiler._qualify_filter(filter_sql, model_name))
        return filters

    def _slot_filter_conditions(
        self,
        filters: list[tuple[SchemaElement, str, Any]],
        model_by_name: dict[str, dict[str, Any]],
        model_name_by_id: dict[int | None, str],
    ) -> list[str]:
        conditions: list[str] = []
        for dimension, operator, value in filters:
            model_name = model_name_by_id.get(dimension.model)
            if not model_name:
                raise ValueError("SEMANTIC_SQL_FILTER_MODEL_REQUIRED")
            expr = self._dimension_expr(model_by_name[model_name], dimension.biz_name)
            conditions.append(f"{self._qualify_expr(expr, model_name)} {self._safe_operator(operator)} {self._literal(value)}")
        return conditions

    @staticmethod
    def _safe_operator(operator: str) -> str:
        normalized = str(operator or "=").strip().lower()
        return normalized if normalized in {"=", "!=", "<>", ">", "<", ">=", "<=", "like"} else "="

    @staticmethod
    def _literal(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    @staticmethod
    def _qualify_filter(filter_sql: str, alias: str) -> str:
        if "." in filter_sql:
            return filter_sql
        return re.sub(r"\b([A-Za-z_][A-Za-z0-9_]*)\b(?=\s*(=|<>|!=|>|<|>=|<=|in\b|like\b))", rf"{alias}.\1", filter_sql)
