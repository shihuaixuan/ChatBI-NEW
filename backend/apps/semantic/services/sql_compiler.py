from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp as sqlglot_exp

from apps.semantic.models.dto import (
    DatasetSchema,
    JoinRelation,
    SchemaElement,
    SemanticPlanStatus,
    SemanticQueryPlan,
)
from apps.semantic.services.builders.schema_builder import build_ontology_from_schema
from apps.semantic.services.compilation import (
    build_ratio_expression,
    decide_time_offset,
    render_preaggregation_subquery,
    render_snapshot_aggregation,
    render_time_offset_expression,
)
from apps.temporal import TemporalSQLRenderError, render_time_filter_condition


@dataclass
class SemanticSQLCompileRequest:
    schema: DatasetSchema
    question: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    metric_ids: list[int] = field(default_factory=list)
    dimension_ids: list[int] = field(default_factory=list)
    repair_context: dict[str, Any] = field(default_factory=dict)
    order_by: list[dict[str, Any]] = field(default_factory=list)
    limit: int | None = None
    time_bucket: dict[str, Any] | None = None
    select_mode: str = "aggregate"
    having: list[dict[str, Any]] = field(default_factory=list)
    time_offset: dict[str, Any] | None = None
    pre_aggregation: dict[str, Any] | None = None
    subplans: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SemanticSQLCompileResult:
    sql: str
    tables: list[str]
    metrics: list[str]
    dimensions: list[str]
    metric_ids: list[int]
    dimension_ids: list[int]
    # 指标级过滤口径的来源标注（metric_id/biz_name/filter_sql），供口径卡片与审计透出。
    metric_filters: list[dict[str, Any]] = field(default_factory=list)


def _same_asset_ids(actual: list[int], expected: list[int]) -> bool:
    """比较资产集合并保留重复项检查，避免顺序差异造成误拒绝。"""

    return len(actual) == len(expected) and sorted(actual) == sorted(expected)


class SemanticSQLCompiler:
    def compile_verified_plan(
        self,
        schema: DatasetSchema,
        plan: SemanticQueryPlan,
    ) -> SemanticSQLCompileResult:
        """只按已验证计划编译，禁止重新匹配或替换语义资产。"""

        if plan.validation_status != SemanticPlanStatus.PROVEN:
            raise ValueError("SEMANTIC_QUERY_PLAN_NOT_PROVEN")
        dimension_ids = [item.physical_dimension_id for item in plan.dimensions]
        time_binding = plan.time_binding
        if time_binding.dimension_id is not None and time_binding.grain:
            time_bucket = {
                "dimension_id": time_binding.dimension_id,
                "grain": time_binding.grain,
            }
        else:
            time_bucket = None
        filters = [
            {
                "asset_type": "DIMENSION",
                "asset_id": item.physical_dimension_id,
                "operator": item.operator,
                "value": item.value,
            }
            for item in plan.filters
        ]
        if time_binding.dimension_id is not None and time_binding.time_range is not None:
            filters.append(
                {
                    "asset_type": "DIMENSION",
                    "asset_id": time_binding.dimension_id,
                    "operator": "=",
                    "value": time_binding.time_range,
                }
            )
        result = self.compile(
            SemanticSQLCompileRequest(
                schema=schema,
                metric_ids=[item.metric_id for item in plan.metrics],
                dimension_ids=dimension_ids,
                slots={"filters": filters},
                order_by=list(plan.order_by),
                limit=plan.limit,
                time_bucket=time_bucket,
                select_mode=str(plan.query_shape.get("select_mode") or "aggregate"),
                having=list(plan.having),
                time_offset=plan.time_offset,
                pre_aggregation={
                    "required": True,
                    "grain": list(plan.model_plan.pre_aggregation_grain),
                }
                if plan.model_plan.pre_aggregation_required
                else None,
                subplans=list(plan.subplans),
            )
        )
        expected_metric_ids = [item.metric_id for item in plan.metrics]
        expected_dimension_ids = [*dimension_ids]
        if time_binding.dimension_id is not None:
            # 时间维度即使只用于过滤、没有分桶，也属于计划绑定的物理资产；
            # 需要透出到 used_assets，供严格入口完成完整性校验。
            if time_binding.dimension_id not in result.dimension_ids:
                result.dimension_ids = [time_binding.dimension_id, *result.dimension_ids]
                time_dimension = next(
                    (
                        item
                        for item in schema.dimensions
                        if item.id == time_binding.dimension_id
                    ),
                    None,
                )
                if time_dimension is not None:
                    result.dimensions = [time_dimension.biz_name, *result.dimensions]
            if time_binding.dimension_id not in expected_dimension_ids:
                expected_dimension_ids = [
                    time_binding.dimension_id,
                    *expected_dimension_ids,
                ]
        # 编译器按 Schema 的稳定顺序输出资产，计划则按用户槽位顺序保存；
        # 严格校验只关心资产集合完全一致，不应把合法的顺序差异误判为资产被替换。
        if not _same_asset_ids(result.metric_ids, expected_metric_ids) or not _same_asset_ids(
            result.dimension_ids,
            expected_dimension_ids,
        ):
            raise ValueError("SEMANTIC_QUERY_PLAN_ASSET_CHANGED")
        return result

    def compile(self, request: SemanticSQLCompileRequest) -> SemanticSQLCompileResult:
        ontology = build_ontology_from_schema(request.schema)
        relations = self._validated_relations(request.schema, ontology.join_relations)
        metrics = self._select_metrics(request)
        dimensions = self._select_dimensions(request)
        filters = self._select_filters(request)
        bucket_dimension, bucket_grain = self._resolve_time_bucket(request)
        if bucket_dimension is not None:
            # 分桶维度独立渲染，不与普通分组维度重复。
            dimensions = [dimension for dimension in dimensions if dimension.id != bucket_dimension.id]
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
        if not metrics and not dimensions and not filter_dimensions and bucket_dimension is None:
            raise ValueError("SEMANTIC_SQL_ASSET_REQUIRED")

        bucket_dimensions = [bucket_dimension] if bucket_dimension is not None else []
        selected_model_names = self._selected_model_names(
            metrics,
            [*dimensions, *filter_dimensions, *bucket_dimensions],
            model_name_by_id,
        )
        if not selected_model_names:
            raise ValueError("SEMANTIC_SQL_MODEL_REQUIRED")

        base_model_name = self._base_model_name(metrics, [*dimensions, *bucket_dimensions], model_name_by_id)
        ordered_model_names = self._order_models(base_model_name, selected_model_names, relations)
        model_sql = {name: self._model_source(model_by_name[name], alias=name) for name in ordered_model_names}

        from_sql = self._build_from_sql(ordered_model_names, model_sql, relations)
        preaggregation_pushed_filters: set[str] = set()
        preaggregated_metric_ids: set[int] = set()
        if request.pre_aggregation and request.pre_aggregation.get("required"):
            (
                model_sql[base_model_name],
                preaggregation_pushed_filters,
                preaggregated_metric_ids,
            ) = self._build_verified_preaggregation_source(
                schema=request.schema,
                base_model_name=base_model_name,
                model=model_by_name[base_model_name],
                metrics=metrics,
                dimensions=dimensions,
                bucket_dimension=bucket_dimension,
                grain=request.pre_aggregation.get("grain") or [],
                filters=filters,
                model_filters=self._model_filters([base_model_name], model_by_name),
                metric_filters=self._metric_filters(
                    metrics, model_by_name, model_name_by_id
                )[0],
                relations=relations,
                selected_model_names=selected_model_names,
                model_by_name=model_by_name,
                model_name_by_id=model_name_by_id,
            )
            from_sql = self._build_from_sql(ordered_model_names, model_sql, relations)
        elif request.pre_aggregation:
            source_sql = str(request.pre_aggregation.get("source_sql") or from_sql)
            select_expressions = request.pre_aggregation.get("select_expressions")
            group_expressions = request.pre_aggregation.get("group_expressions")
            if not isinstance(select_expressions, list) or not isinstance(group_expressions, list):
                raise ValueError("SEMANTIC_SQL_PRE_AGGREGATION_PLAN_REQUIRED")
            from_sql = render_preaggregation_subquery(
                source_sql,
                select_expressions=[str(item) for item in select_expressions],
                group_expressions=[str(item) for item in group_expressions],
                alias=str(request.pre_aggregation.get("alias") or "preagg"),
            )
        detail_mode = str(request.select_mode or "").strip().lower() == "detail"
        select_parts = [
            f"{self._qualified_dimension_expr(dimension, model_by_name, model_name_by_id)} as {dimension.biz_name}"
            for dimension in dimensions
        ]
        if detail_mode:
            metric_selects = [
                (metric, self._metric_detail_expr(metric, model_by_name, model_name_by_id), False)
                for metric in metrics
            ]
        else:
            metric_selects = [
                (
                    metric,
                    *self._metric_select_expr(
                        metric,
                        model_by_name,
                        model_name_by_id,
                        metric_by_id={item.id: item for item in request.schema.metrics},
                    ),
                )
                for metric in metrics
            ]
        metric_filter_conditions, metric_filter_sources = self._metric_filters(
            metrics,
            model_by_name,
            model_name_by_id,
        )
        where_parts = [
            *self._model_filters(ordered_model_names, model_by_name),
            *self._slot_filter_conditions(filters, model_by_name, model_name_by_id),
            *metric_filter_conditions,
        ]
        if preaggregation_pushed_filters:
            where_parts = [
                item for item in where_parts if item not in preaggregation_pushed_filters
            ]
        if preaggregated_metric_ids:
            # 外层只汇总预聚合结果，不能再次引用基础模型的明细表达式。
            updated_metric_selects = []
            for metric, expression, is_aggregate in metric_selects:
                if metric.id in preaggregated_metric_ids:
                    updated_metric_selects.append(
                        (
                            metric,
                            f"sum({model_name_by_id[metric.model]}.{metric.biz_name})",
                            True,
                        )
                    )
                else:
                    updated_metric_selects.append((metric, expression, is_aggregate))
            metric_selects = updated_metric_selects
        group_parts = [self._qualified_dimension_expr(dimension, model_by_name, model_name_by_id) for dimension in dimensions]
        if bucket_dimension is not None:
            bucket_expr = self._time_bucket_expr(
                self._qualified_dimension_expr(bucket_dimension, model_by_name, model_name_by_id),
                bucket_grain,
                request.schema.database_type,
            )
            select_parts.insert(0, f"{bucket_expr} as {bucket_dimension.biz_name}")
            group_parts.insert(0, bucket_expr)

        metric_selects = self._apply_snapshot_metric_selects(
            metric_selects,
            bucket_expr if bucket_dimension is not None else None,
        )
        select_parts.extend(f"{metric_expr} as {metric.biz_name}" for metric, metric_expr, _ in metric_selects)
        self._append_time_offset_selects(
            select_parts,
            metric_selects,
            request,
            bucket_dimension,
            bucket_grain,
        )

        sql = f"select {', '.join(select_parts)} from {from_sql}"
        if where_parts:
            sql += " where " + " and ".join(where_parts)
        if not detail_mode and any(is_aggregate for _, _, is_aggregate in metric_selects) and group_parts:
            sql += " group by " + ", ".join(group_parts)
        having_parts = [] if detail_mode else self._having_parts(request.having, metric_selects)
        if having_parts:
            sql += " having " + " and ".join(having_parts)
        order_parts = self._order_parts(request.order_by, metrics, [*dimensions, *bucket_dimensions])
        if bucket_dimension is not None and not order_parts:
            # 趋势结果默认按时间桶升序返回。
            order_parts = [f"{bucket_dimension.biz_name} asc"]
        if order_parts:
            sql += " order by " + ", ".join(order_parts)
        if request.limit:
            sql += f" limit {request.limit}"

        return SemanticSQLCompileResult(
            sql=sql,
            tables=[self._model_table(model_by_name[name]) for name in ordered_model_names if self._model_table(model_by_name[name])],
            metrics=[metric.biz_name for metric in metrics],
            dimensions=[dimension.biz_name for dimension in [*bucket_dimensions, *dimensions]],
            # 资产名称在跨模型场景下不唯一，必须把编译器实际选择的 ID 原样带出。
            metric_ids=[metric.id for metric in metrics],
            dimension_ids=[
                dimension.id for dimension in [*bucket_dimensions, *dimensions]
            ],
            metric_filters=metric_filter_sources,
        )

    _SQLGLOT_DIALECTS = {
        "mysql": "mysql",
        "mariadb": "mysql",
        "tidb": "mysql",
        "postgresql": "postgres",
        "postgres": "postgres",
        "pg": "postgres",
        "kingbase": "postgres",
        "doris": "doris",
        "starrocks": "starrocks",
        "clickhouse": "clickhouse",
        "oracle": "oracle",
        "sqlserver": "tsql",
        "mssql": "tsql",
    }
    _TIME_BUCKET_GRAINS = {"day", "week", "month", "quarter", "year"}

    @classmethod
    def _resolve_time_bucket(cls, request: SemanticSQLCompileRequest) -> tuple[SchemaElement | None, str]:
        bucket = request.time_bucket if isinstance(request.time_bucket, dict) else None
        if not bucket:
            return None, ""
        grain = str(bucket.get("grain") or "").strip().lower()
        if grain not in cls._TIME_BUCKET_GRAINS:
            raise ValueError("SEMANTIC_SQL_TIME_GRAIN_UNSUPPORTED")
        dimension_id = bucket.get("dimension_id")
        dimension = next((item for item in request.schema.dimensions if item.id == dimension_id), None)
        if dimension is None:
            raise ValueError("SEMANTIC_SQL_TIME_BUCKET_DIMENSION_NOT_FOUND")
        return dimension, grain

    @classmethod
    def _time_bucket_expr(cls, expr: str, grain: str, database_type: str | None) -> str:
        """按数据源方言渲染时间分桶表达式（sqlglot 转译 DATE_TRUNC）。"""

        dialect = cls._SQLGLOT_DIALECTS.get(str(database_type or "").strip().lower(), "mysql")
        try:
            column = sqlglot.parse_one(expr)
            node = sqlglot_exp.DateTrunc(this=column, unit=sqlglot_exp.Literal.string(grain.upper()))
            return node.sql(dialect=dialect)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("SEMANTIC_SQL_TIME_BUCKET_UNSUPPORTED") from exc

    def _select_metrics(self, request: SemanticSQLCompileRequest) -> list[SchemaElement]:
        metric_ids = set(request.metric_ids or self._slot_asset_ids(request.slots, "metrics", "METRIC"))
        if metric_ids:
            return [metric for metric in request.schema.metrics if metric.id in metric_ids]
        return self._match_elements(request.question, request.schema.metrics)

    def _select_dimensions(self, request: SemanticSQLCompileRequest) -> list[SchemaElement]:
        if request.dimension_ids:
            dimension_ids = set(request.dimension_ids)
        elif "dimensions" in request.slots or "dimension" in request.slots:
            dimension_ids = set(self._slot_asset_ids(request.slots, "dimensions", "DIMENSION"))
            return [dimension for dimension in request.schema.dimensions if dimension.id in dimension_ids]
        else:
            dimension_ids = set()
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
    def _validated_relations(
        schema: DatasetSchema,
        relations: list[JoinRelation],
    ) -> list[JoinRelation]:
        """优先消费关系契约，拒绝会造成指标传播风险的 join。"""

        contracts = [item for item in schema.relation_contracts if isinstance(item, dict)]
        if not contracts:
            return relations
        model_names = {
            int(item.get("id")): str(item.get("biz_name") or item.get("name") or "")
            for item in schema.models
            if item.get("id") is not None
        }
        by_pair = {
            frozenset({model_names.get(int(item.get("left_model_id") or 0), ""), model_names.get(int(item.get("right_model_id") or 0), "")}): item
            for item in contracts
            if item.get("left_model_id") is not None and item.get("right_model_id") is not None
        }
        for relation in relations:
            contract = by_pair.get(frozenset({relation.left, relation.right}))
            if contract is None:
                raise ValueError("SEMANTIC_SQL_RELATION_CONTRACT_REQUIRED")
            if contract.get("contract_status") not in {None, "READY"}:
                raise ValueError("SEMANTIC_SQL_RELATION_CONTRACT_NOT_READY")
            if contract.get("cardinality") == "MANY_TO_MANY":
                raise ValueError("SEMANTIC_SQL_MANY_TO_MANY_JOIN_FORBIDDEN")
            if contract.get("aggregation_safety") == "FORBIDDEN":
                raise ValueError("SEMANTIC_SQL_JOIN_AGGREGATION_FORBIDDEN")
            if str(contract.get("metric_propagation") or "").upper() == "NONE":
                raise ValueError("SEMANTIC_SQL_METRIC_PROPAGATION_FORBIDDEN")
        return relations

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

    def _build_verified_preaggregation_source(
        self,
        *,
        schema: DatasetSchema,
        base_model_name: str,
        model: dict[str, Any],
        metrics: list[SchemaElement],
        dimensions: list[SchemaElement],
        bucket_dimension: SchemaElement | None,
        grain: list[Any],
        filters: list[tuple[SchemaElement, str, Any]],
        model_filters: list[str],
        metric_filters: list[str],
        relations: list[JoinRelation],
        selected_model_names: list[str],
        model_by_name: dict[str, dict[str, Any]],
        model_name_by_id: dict[int | None, str],
    ) -> tuple[str, set[str], set[int]]:
        """在连接维表前按基础模型粒度聚合，避免一对多连接放大指标。"""

        if not grain:
            raise ValueError("SEMANTIC_SQL_PRE_AGGREGATION_GRAIN_REQUIRED")
        if any(metric.model != model.get("id") for metric in metrics):
            raise ValueError("SEMANTIC_SQL_PRE_AGGREGATION_BASE_MODEL_REQUIRED")
        if any(self._metric_reference_ids(metric) for metric in metrics):
            raise ValueError("SEMANTIC_SQL_PRE_AGGREGATION_DERIVED_METRIC_UNSUPPORTED")
        if any(str((metric.ext_info or {}).get("snapshot_aggregation") or "").strip() for metric in metrics):
            raise ValueError("SEMANTIC_SQL_PRE_AGGREGATION_SNAPSHOT_UNSUPPORTED")

        base_element_by_name = {
            str(item.get("bizName") or item.get("biz_name")): item
            for item in model.get("dimensions") or []
            if isinstance(item, dict)
        }
        group_fields: dict[str, str] = {}

        def add_group_field(expression: str) -> None:
            normalized = str(expression or "").strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
                raise ValueError("SEMANTIC_SQL_PRE_AGGREGATION_FIELD_UNSAFE")
            group_fields.setdefault(normalized, normalized)

        for item in grain:
            grain_name = str(item or "").strip()
            expression = str(
                (base_element_by_name.get(grain_name) or {}).get("expr") or grain_name
            )
            add_group_field(expression)

        for dimension in [*dimensions, *([bucket_dimension] if bucket_dimension else [])]:
            if dimension.model != model.get("id"):
                continue
            add_group_field(self._dimension_expr(model, dimension.biz_name))

        for candidate in selected_model_names:
            if candidate == base_model_name:
                continue
            relation = self._find_relation([base_model_name], candidate, relations)
            if relation is None:
                raise ValueError("SEMANTIC_SQL_PRE_AGGREGATION_RELATION_PATH_UNSUPPORTED")
            if relation.left == base_model_name:
                base_side = relation.left
                fields = [condition[0] for condition in relation.join_condition]
            else:
                base_side = relation.right
                fields = [condition[2] for condition in relation.join_condition]
            if base_side != base_model_name:
                raise ValueError("SEMANTIC_SQL_PRE_AGGREGATION_RELATION_PATH_UNSUPPORTED")
            for join_field in fields:
                add_group_field(join_field)

        source_alias = "preagg_source"
        source_sql = self._model_source(model, source_alias)
        select_parts = [
            f"{source_alias}.{field} AS {field}" for field in group_fields
        ]
        preaggregated_metric_ids: set[int] = set()
        for metric in metrics:
            raw_expression, measure_agg = self._metric_measure_expr(metric, model)
            if self._contains_aggregate(raw_expression):
                raise ValueError("SEMANTIC_SQL_PRE_AGGREGATION_METRIC_UNSUPPORTED")
            resolved_agg = self._resolve_metric_agg(metric, measure_agg)
            if resolved_agg.upper() != "SUM":
                raise ValueError("SEMANTIC_SQL_PRE_AGGREGATION_METRIC_UNSUPPORTED")
            qualified = self._qualify_expr(raw_expression, source_alias)
            select_parts.append(
                f"{self._aggregate_expr(resolved_agg, qualified)} AS {metric.biz_name}"
            )
            preaggregated_metric_ids.add(metric.id)

        slot_conditions = self._slot_filter_conditions(
            filters,
            model_by_name,
            model_name_by_id,
        )
        base_filter_conditions = [
            condition
            for (dimension, _, _), condition in zip(filters, slot_conditions, strict=True)
            if model_name_by_id.get(dimension.model) == base_model_name
        ]
        pushed_filter_list = list(
            dict.fromkeys([*model_filters, *metric_filters, *base_filter_conditions])
        )
        inner_filters = [
            re.sub(rf"\b{re.escape(base_model_name)}\.", f"{source_alias}.", item)
            for item in pushed_filter_list
        ]
        sql = (
            f"(select {', '.join(select_parts)} from {source_sql}"
            + (" where " + " and ".join(inner_filters) if inner_filters else "")
            + f" group by {', '.join(f'{source_alias}.{field}' for field in group_fields)}) {base_model_name}"
        )
        return sql, set(pushed_filter_list), preaggregated_metric_ids

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
        return self._metric_select_expr(metric, model_by_name, model_name_by_id)[0]

    def _metric_select_expr(
        self,
        metric: SchemaElement,
        model_by_name: dict[str, dict[str, Any]],
        model_name_by_id: dict[int | None, str],
        *,
        metric_by_id: dict[int, SchemaElement] | None = None,
        stack: tuple[int, ...] = (),
    ) -> tuple[str, bool]:
        model_name = model_name_by_id.get(metric.model)
        if not model_name:
            raise ValueError("SEMANTIC_SQL_METRIC_MODEL_REQUIRED")
        refs = self._metric_reference_ids(metric)
        if refs:
            if metric.id in stack:
                raise ValueError("SEMANTIC_SQL_METRIC_REFERENCE_CYCLE")
            if metric_by_id is None or any(ref not in metric_by_id for ref in refs):
                raise ValueError("SEMANTIC_SQL_METRIC_REFERENCE_NOT_FOUND")
            rendered_refs = {
                ref: self._metric_select_expr(
                    metric_by_id[ref],
                    model_by_name,
                    model_name_by_id,
                    metric_by_id=metric_by_id,
                    stack=(*stack, metric.id),
                )[0]
                for ref in refs
            }
            expression = self._derived_metric_expression(metric, refs, rendered_refs, metric_by_id)
            return expression, True
        expr, agg = self._metric_measure_expr(metric, model_by_name[model_name])
        qualified = self._qualify_expr(expr, model_name)
        if self._contains_aggregate(expr):
            return qualified, True
        resolved_agg = self._resolve_metric_agg(metric, agg)
        return self._aggregate_expr(resolved_agg, qualified), self._is_aggregate_agg(resolved_agg)

    @staticmethod
    def _metric_reference_ids(metric: SchemaElement) -> tuple[int, ...]:
        params = metric.type_params or {}
        metric_params = params.get("metricDefineByMetricParams") or {}
        references = metric_params.get("metrics") if isinstance(metric_params, dict) else []
        ids = [item.get("id") for item in references or [] if isinstance(item, dict)]
        if not ids and str(params.get("metricDefineType") or "").upper() == "METRIC":
            ids = metric.ext_info.get("metric_refs") if isinstance(metric.ext_info, dict) else []
        return tuple(item for item in ids if isinstance(item, int) and item > 0)

    def _derived_metric_expression(
        self,
        metric: SchemaElement,
        refs: tuple[int, ...],
        rendered_refs: dict[int, str],
        metric_by_id: dict[int, SchemaElement],
    ) -> str:
        params = metric.type_params or {}
        metric_params = params.get("metricDefineByMetricParams") or {}
        expression = str(metric_params.get("expr") or metric.ext_info.get("expr") or "").strip()
        if not expression:
            if len(refs) != 2:
                raise ValueError("SEMANTIC_SQL_DERIVED_METRIC_EXPRESSION_REQUIRED")
            return build_ratio_expression(rendered_refs[refs[0]], rendered_refs[refs[1]])
        for ref in sorted(refs, key=lambda item: len(str(item)), reverse=True):
            target = metric_by_id[ref]
            names = {
                str(target.biz_name),
                str(target.name),
                f"metric_{ref}",
            }
            for name in sorted(names, key=len, reverse=True):
                expression = re.sub(
                    rf"\b{re.escape(name)}\b",
                    f"({rendered_refs[ref]})",
                    expression,
                )
        if any(
            token in expression.lower()
            for token in (";", "--", "/*", "*/", "select ", " from ")
        ):
            raise ValueError("SEMANTIC_SQL_DERIVED_METRIC_EXPRESSION_UNSAFE")
        if len(refs) == 2 and "/" in expression and "nullif" not in expression.lower():
            return build_ratio_expression(rendered_refs[refs[0]], rendered_refs[refs[1]])
        return expression

    def _append_time_offset_selects(
        self,
        select_parts: list[str],
        metric_selects: list[tuple[SchemaElement, str, bool]],
        request: SemanticSQLCompileRequest,
        bucket_dimension: SchemaElement | None,
        bucket_grain: str,
    ) -> None:
        if not request.time_offset:
            return
        method = str(request.time_offset.get("method") or "").strip().lower()
        grain = str(request.time_offset.get("grain") or bucket_grain or "").strip().lower()
        decision = decide_time_offset(
            method=method,
            grain=grain,
            range_count=int(request.time_offset.get("range_count") or 1),
        )
        if decision.mode != "single_sql":
            raise ValueError("SEMANTIC_SQL_TIME_OFFSET_REQUIRES_DUAL_QUERY")
        time_alias = str(
            request.time_offset.get("time_alias")
            or (bucket_dimension.biz_name if bucket_dimension is not None else "")
        ).strip()
        if not time_alias:
            raise ValueError("SEMANTIC_SQL_TIME_OFFSET_TIME_DIMENSION_REQUIRED")
        for metric, expression, _ in metric_selects:
            previous = render_time_offset_expression(
                expression,
                time_alias=time_alias,
                periods=decision.periods,
            )
            select_parts.append(f"{previous} as {metric.biz_name}_previous")

    @staticmethod
    def _apply_snapshot_metric_selects(
        metric_selects: list[tuple[SchemaElement, str, bool]],
        time_expression: str | None,
    ) -> list[tuple[SchemaElement, str, bool]]:
        """有时间分桶时把快照策略渲染为窗口/聚合表达式。"""

        if not time_expression:
            return metric_selects
        result: list[tuple[SchemaElement, str, bool]] = []
        for metric, expression, is_aggregate in metric_selects:
            strategy = str((metric.ext_info or {}).get("snapshot_aggregation") or "").strip()
            if not strategy:
                result.append((metric, expression, is_aggregate))
                continue
            rendered = render_snapshot_aggregation(expression, time_expression, strategy)
            result.append((metric, rendered, True))
        return result

    def _metric_detail_expr(
        self,
        metric: SchemaElement,
        model_by_name: dict[str, dict[str, Any]],
        model_name_by_id: dict[int | None, str],
    ) -> str:
        """明细模式投影指标原始表达式，不自动套聚合函数。"""

        model_name = model_name_by_id.get(metric.model)
        if not model_name:
            raise ValueError("SEMANTIC_SQL_METRIC_MODEL_REQUIRED")
        expr, _ = self._metric_measure_expr(metric, model_by_name[model_name])
        return self._qualify_expr(expr, model_name)

    @staticmethod
    def _metric_measure_expr(metric: SchemaElement, model: dict[str, Any]) -> tuple[str, str | None]:
        params = metric.type_params or {}
        measure_params = params.get("metricDefineByMeasureParams") or {}
        derived_expr = measure_params.get("expr") if isinstance(measure_params, dict) else None
        if derived_expr and SemanticSQLCompiler._contains_aggregate(str(derived_expr)):
            return str(derived_expr), "NONE"
        measures = measure_params.get("measures") if isinstance(measure_params, dict) else []
        if measures:
            measure = measures[0]
            return measure.get("expr") or measure.get("bizName") or metric.biz_name, measure.get("agg")
        for measure in model.get("measures") or []:
            if (measure.get("bizName") or measure.get("biz_name")) == metric.biz_name:
                return measure.get("expr") or metric.biz_name, measure.get("agg")
        return metric.biz_name, metric.default_agg

    @staticmethod
    def _order_parts(
        order_by: list[dict[str, Any]],
        metrics: list[SchemaElement],
        dimensions: list[SchemaElement],
    ) -> list[str]:
        """把受控排序槽位转换为输出别名排序，避免注入任意表达式。"""

        alias_by_id = {element.id: element.biz_name for element in [*metrics, *dimensions]}
        allowed_aliases = set(alias_by_id.values())
        parts: list[str] = []
        for item in order_by:
            if not isinstance(item, dict):
                continue
            asset_id = item.get("asset_id")
            alias = alias_by_id.get(asset_id)
            if alias is None:
                candidate = str(item.get("biz_name") or "").strip()
                alias = candidate if candidate in allowed_aliases else None
            if not alias:
                continue
            direction = "asc" if str(item.get("direction") or "").lower() == "asc" else "desc"
            parts.append(f"{alias} {direction}")
        return parts

    @staticmethod
    def _having_parts(
        having: list[dict[str, Any]],
        metric_selects: list[tuple[SchemaElement, str, bool]],
    ) -> list[str]:
        """把受控指标阈值过滤转换为 HAVING，禁止自由表达式进入 SQL。"""

        metric_expr_by_id = {metric.id: metric_expr for metric, metric_expr, _ in metric_selects}
        parts: list[str] = []
        for item in having:
            if not isinstance(item, dict):
                continue
            if str(item.get("asset_type") or "").upper() not in {"", "METRIC"}:
                continue
            metric_expr = metric_expr_by_id.get(item.get("asset_id"))
            if not metric_expr:
                continue
            operator = SemanticSQLCompiler._safe_operator(str(item.get("operator") or "="))
            parts.append(f"{metric_expr} {operator} {SemanticSQLCompiler._literal(item.get('value'))}")
        return parts

    @staticmethod
    def _contains_aggregate(expr: str) -> bool:
        return re.search(r"\b(sum|count|avg|min|max)\s*\(", expr, flags=re.IGNORECASE) is not None

    @staticmethod
    def _resolve_metric_agg(metric: SchemaElement, measure_agg: str | None) -> str:
        metric_agg = str(metric.default_agg or "").strip()
        if metric_agg.upper() == "NONE":
            return "NONE"
        return measure_agg or metric_agg or "SUM"

    @staticmethod
    def _is_aggregate_agg(agg: str) -> bool:
        return str(agg or "").upper() in {"SUM", "COUNT", "AVG", "MIN", "MAX", "COUNT_DISTINCT"}

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

    @classmethod
    def _metric_filters(
        cls,
        metrics: list[SchemaElement],
        model_by_name: dict[str, dict[str, Any]],
        model_name_by_id: dict[int | None, str],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """把指标级 filter_sql 合并进 WHERE，并返回来源标注。

        多个指标声明了不同过滤口径时，单条 SQL 无法同时满足，
        必须在编译边界显式失败（完整 FILTER(WHERE) 方言支持在 P1 补齐）。
        """

        conditions: list[str] = []
        sources: list[dict[str, Any]] = []
        seen_condition: set[str] = set()
        distinct_filters: set[str] = set()
        for metric in metrics:
            filter_sql = str((metric.ext_info or {}).get("filter_sql") or "").strip()
            if not filter_sql:
                continue
            distinct_filters.add(filter_sql)
            model_name = model_name_by_id.get(metric.model)
            if not model_name:
                raise ValueError("SEMANTIC_SQL_METRIC_MODEL_REQUIRED")
            condition = cls._qualify_filter(filter_sql, model_name)
            if condition not in seen_condition:
                seen_condition.add(condition)
                conditions.append(condition)
            sources.append(
                {
                    "metric_id": metric.id,
                    "biz_name": metric.biz_name,
                    "model": model_name,
                    "filter_sql": filter_sql,
                }
            )
        if len(distinct_filters) > 1:
            raise ValueError("SEMANTIC_SQL_METRIC_FILTER_CONFLICT")
        return conditions, sources

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
            qualified_expr = self._qualify_expr(expr, model_name)
            try:
                time_condition = render_time_filter_condition(qualified_expr, value)
            except TemporalSQLRenderError as exc:
                # 语义编译边界保留稳定错误码，具体日期校验由 temporal 统一负责。
                raise ValueError("SEMANTIC_SQL_TIME_RANGE_UNSUPPORTED") from exc
            if time_condition is not None:
                conditions.append(time_condition)
                continue
            normalized_operator = str(operator or "=").strip().lower()
            if normalized_operator in {"in", "not in"}:
                if not isinstance(value, list) or not value:
                    raise ValueError("SEMANTIC_SQL_FILTER_VALUES_REQUIRED")
                literals = ", ".join(self._literal(item) for item in value)
                conditions.append(
                    f"{qualified_expr} {normalized_operator} ({literals})"
                )
                continue
            conditions.append(f"{qualified_expr} {self._safe_operator(operator)} {self._literal(value)}")
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
