from __future__ import annotations

from typing import Any

from apps.agentic_chat.strategies.sql_repair import SQLRepairStrategy
from apps.agentic_chat.tools.sql_executor import SqlExecuteTool
from apps.agentic_chat.tools.sql_validator import SqlValidateTool
from apps.chatbi_workflow.capabilities.adapters.permission import PermissionAdapter
from apps.chatbi_workflow.capabilities.context import ChatBIRunContext
from apps.headless.service import HeadlessSchemaBuilder
from apps.headless.sql_compiler import (
    SemanticSQLCompiler,
    SemanticSQLCompileRequest,
)


class SqlAdapter:
    """ChatBI v1 SQL 生成适配器，复用 Headless 语义 SQL 编译器。"""

    def __init__(
        self,
        schema_builder: HeadlessSchemaBuilder | None = None,
        compiler: SemanticSQLCompiler | None = None,
        execute_tool: SqlExecuteTool | None = None,
        validate_tool: SqlValidateTool | None = None,
        permission_adapter: PermissionAdapter | None = None,
        repair_strategy: SQLRepairStrategy | None = None,
        sample_row_limit: int = 50,
    ) -> None:
        self._schema_builder = schema_builder or HeadlessSchemaBuilder()
        self._compiler = compiler or SemanticSQLCompiler()
        self._execute_tool = execute_tool
        self._validate_tool = validate_tool or SqlValidateTool()
        self._permission_adapter = permission_adapter or PermissionAdapter()
        self._repair_strategy = repair_strategy or SQLRepairStrategy()
        self._sample_row_limit = max(sample_row_limit, 0)

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
        knowledge = ctx.knowledge
        question = ctx.question
        dataset_id = ctx.dataset_id
        if not question or dataset_id is None:
            raise ValueError("SQL_GENERATE_CONTEXT_REQUIRED")

        schema = self._schema_builder.build_dataset_schema(ctx.tenant_id, dataset_id)
        intent = ctx.intent
        slots = self._compile_slots(knowledge, intent)
        order_by, limit = self._compile_order_and_limit(intent, slots)
        repair_context = self._repair_context(ctx)
        result = self._compiler.compile(
            SemanticSQLCompileRequest(
                schema=schema,
                question=question,
                slots=slots,
                repair_context=repair_context,
                order_by=order_by,
                limit=limit,
            )
        )
        self._reject_same_repair_sql(result.sql, repair_context)
        validated = self._validate_tool.run({"sql": result.sql, "allowed_tables": result.tables})
        if not validated.success:
            raise ValueError(validated.error_code or "SQL_VALIDATE_FAILED")
        validated_sql = (validated.payload or {}).get("sql") or result.sql
        return {
            "sql": validated_sql,
            "strategy": "semantic_sql_compiler",
            "datasource_id": self._datasource_id(schema, result.metrics, result.dimensions),
            "explanation": "基于 Headless 语义资产生成 SQL",
            "used_assets": [
                *self._used_assets("METRIC", result.metrics, schema.metrics),
                *self._used_assets("DIMENSION", result.dimensions, schema.dimensions),
            ],
        }

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
        sql_info = ctx.sql
        sql = str(sql_info.get("sql") or "").strip()
        datasource_id = self._int_or_none(sql_info.get("datasource_id"))
        if not sql or datasource_id is None:
            return self._failed("SQL_EXECUTE_CONTEXT_REQUIRED", "缺少 SQL 或 datasource_id")
        if self._execute_tool is None:
            return self._failed("SQL_EXECUTE_TOOL_REQUIRED", "SQL 执行工具未配置")
        permission = self._permission_adapter.apply(
            {
                "sql": sql,
                "datasource_id": datasource_id,
                "tenant_id": ctx.request_value("tenant_id"),
                "user_id": ctx.request_value("user_id"),
            }
        )
        if not permission.get("allowed"):
            return self._failed(
                str(permission.get("error_code") or "permission_denied"),
                str(permission.get("reason") or "权限校验拒绝"),
            )
        sql = str(permission.get("sql") or sql)

        result = self._execute_tool.run({"sql": sql, "datasource_id": datasource_id})
        if not result.success:
            return self._failed(result.error_code or "SQL_EXECUTE_FAILED", result.message or "SQL 执行失败")
        payload = result.payload or {}
        rows = payload.get("data") or payload.get("rows") or []
        fields = payload.get("fields") or []
        row_count = int(payload.get("row_count") or len(rows))
        sample_rows = rows[: self._sample_row_limit]
        return {
            "status": "succeeded",
            "rows": sample_rows,
            "row_count": row_count,
            "fields": fields,
            "execution_ms": int(payload.get("execution_ms") or 0),
            "sampled_row_count": len(sample_rows),
            "result_truncated": row_count > len(sample_rows),
            "artifact_ref": payload.get("artifact_ref"),
            "error_code": None,
            "message": None,
        }

    def generate_split(self, request: dict[str, Any]) -> dict[str, Any]:
        """分别编译跨模型子查询，但不在 SQL 生成节点执行查询。"""
        ctx = ChatBIRunContext(request)
        plans = ctx.knowledge.get("multi_query_plans")
        dataset_id = ctx.dataset_id
        if dataset_id is None or not isinstance(plans, list) or len(plans) < 2:
            raise ValueError("CROSS_MODEL_PLAN_REQUIRED")

        schema = self._schema_builder.build_dataset_schema(ctx.tenant_id, dataset_id)
        queries: list[dict[str, Any]] = []
        for plan in plans:
            slots = plan.get("slots") if isinstance(plan.get("slots"), dict) else {}
            compiled = self._compiler.compile(
                SemanticSQLCompileRequest(
                    schema=schema,
                    question=ctx.raw_question,
                    slots=slots,
                )
            )
            validated = self._validate_tool.run({"sql": compiled.sql, "allowed_tables": compiled.tables})
            if not validated.success:
                raise ValueError(validated.error_code or "SQL_VALIDATE_FAILED")
            sql = (validated.payload or {}).get("sql") or compiled.sql
            datasource_id = self._datasource_id(schema, compiled.metrics, compiled.dimensions)
            queries.append(
                {
                    "model_id": plan.get("model_id"),
                    "metrics": plan.get("metrics") or compiled.metrics,
                    "dimensions": plan.get("dimensions") or compiled.dimensions,
                    "sql": sql,
                    "datasource_id": datasource_id,
                }
            )
        return {
            "queries": queries,
            "strategy": "semantic_sql_compiler",
            "explanation": "按模型分别生成 SQL",
        }

    def execute_split(self, request: dict[str, Any]) -> dict[str, Any]:
        """逐条执行已生成的跨模型 SQL，不承担 SQL 编译职责。"""

        ctx = ChatBIRunContext(request)
        queries = ctx.split_sql.get("queries")
        if not isinstance(queries, list) or len(queries) < 2:
            return self._failed("CROSS_MODEL_SQL_REQUIRED", "缺少已生成的跨模型 SQL")
        if self._execute_tool is None:
            return self._failed("SQL_EXECUTE_TOOL_REQUIRED", "SQL 执行工具未配置")

        query_results: list[dict[str, Any]] = []
        total_rows = 0
        total_execution_ms = 0
        for query in queries:
            sql = str(query.get("sql") or "").strip()
            datasource_id = self._int_or_none(query.get("datasource_id"))
            if not sql or datasource_id is None:
                return self._failed("CROSS_MODEL_SQL_INVALID", "跨模型 SQL 缺少 SQL 或 datasource_id")
            permission = self._permission_adapter.apply(
                {
                    "sql": sql,
                    "datasource_id": datasource_id,
                    "tenant_id": ctx.request_value("tenant_id"),
                    "user_id": ctx.request_value("user_id"),
                }
            )
            if not permission.get("allowed"):
                return self._failed(
                    str(permission.get("error_code") or "permission_denied"),
                    str(permission.get("reason") or "权限校验拒绝"),
                )
            execution = self._execute_tool.run(
                {
                    "sql": str(permission.get("sql") or sql),
                    "datasource_id": datasource_id,
                }
            )
            if not execution.success:
                return self._failed(execution.error_code or "SQL_EXECUTE_FAILED", execution.message or "拆分查询执行失败")
            payload = execution.payload or {}
            rows = payload.get("data") or payload.get("rows") or []
            row_count = int(payload.get("row_count") or len(rows))
            execution_ms = int(payload.get("execution_ms") or 0)
            total_rows += row_count
            total_execution_ms += execution_ms
            query_results.append(
                {
                    "model_id": query.get("model_id"),
                    "metrics": query.get("metrics") or [],
                    "dimensions": query.get("dimensions") or [],
                    "rows": rows[: self._sample_row_limit],
                    "row_count": row_count,
                }
            )

        return {
            "status": "succeeded",
            "rows": query_results,
            "row_count": total_rows,
            "fields": ["model_id", "metrics", "dimensions", "rows", "row_count"],
            "execution_ms": total_execution_ms,
            "sampled_row_count": len(query_results),
            "result_truncated": False,
            "artifact_ref": None,
            "error_code": None,
            "message": None,
        }

    def handle_error(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
        execution = ctx.sql_execution
        error_code = str(execution.get("error_code") or "SQL_EXECUTION_FAILED")
        raw_message = str(execution.get("message") or "SQL 执行失败")
        repair = self._repair_strategy.decide(
            error_code=error_code,
            message=raw_message,
            sql=str(ctx.sql.get("sql") or ""),
            knowledge=ctx.knowledge,
        )
        return {
            "error_code": error_code,
            "message": f"SQL 执行失败：{raw_message}",
            "retryable": repair.retryable,
            "repair_hint": repair.repair_hint,
            "repair_plan": repair.plan(),
        }

    def _compile_slots(self, knowledge: dict[str, Any], intent: dict[str, Any] | None = None) -> dict[str, Any]:
        slot_bindings = knowledge.get("slot_bindings") if isinstance(knowledge.get("slot_bindings"), dict) else {}
        selected_assets = knowledge.get("selected_assets") if isinstance(knowledge.get("selected_assets"), dict) else {}
        filters = self._compile_filter_slots(slot_bindings, selected_assets)
        dimensions = self._compile_dimension_slots(slot_bindings, selected_assets, filters)
        if not self._should_select_dimensions(intent):
            dimensions = []
        return {
            "metrics": self._asset_slots(slot_bindings, selected_assets, "metrics", "METRIC"),
            "dimensions": dimensions,
            "filters": filters,
        }

    def _compile_filter_slots(
        self,
        slot_bindings: dict[str, Any],
        selected_assets: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """优先使用已拆分过滤字段；旧上下文继续读取 filters。"""

        separated_filter_keys = ("value_filters", "dimension_filters", "time_filters")
        if any(key in slot_bindings for key in separated_filter_keys):
            return [
                *self._asset_slots(slot_bindings, {}, "value_filters", "VALUE"),
                *self._asset_slots(slot_bindings, {}, "dimension_filters", "DIMENSION"),
                *self._asset_slots(slot_bindings, {}, "time_filters", "DIMENSION"),
            ]
        return self._asset_slots(slot_bindings, selected_assets, "filters", "DIMENSION")

    def _compile_dimension_slots(
        self,
        slot_bindings: dict[str, Any],
        selected_assets: dict[str, Any],
        filters: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """新上下文只把 group_dimensions 作为 SQL 维度，避免时间维度进 GROUP BY。"""

        if "group_dimensions" in slot_bindings:
            return self._asset_slots(slot_bindings, {}, "group_dimensions", "DIMENSION")
        if "business_dimensions" in selected_assets:
            return self._asset_slots({}, selected_assets, "business_dimensions", "DIMENSION")
        return self._dimensions_without_filter_only_assets(
            self._asset_slots(slot_bindings, selected_assets, "dimensions", "DIMENSION"),
            filters,
        )

    @classmethod
    def _compile_order_and_limit(
        cls,
        intent: dict[str, Any],
        slots: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], int | None]:
        """把排名查询形态绑定到已选择的指标，禁止使用未落地的自由字段。"""

        query_shape = intent.get("query_shape") if isinstance(intent.get("query_shape"), dict) else {}
        raw_limit = query_shape.get("limit")
        limit = cls._int_or_none(raw_limit)
        if limit is not None and not 1 <= limit <= 1000:
            limit = None
        if not bool(query_shape.get("needs_order_by")):
            return [], limit
        metrics = slots.get("metrics") if isinstance(slots.get("metrics"), list) else []
        if not metrics:
            return [], limit
        metric_id = cls._int_or_none(metrics[0].get("asset_id"))
        if metric_id is None:
            return [], limit
        direction = "asc" if str(query_shape.get("order_direction") or "").lower() == "asc" else "desc"
        return [
            {
                "asset_type": "METRIC",
                "asset_id": metric_id,
                "direction": direction,
            }
        ], limit

    @staticmethod
    def _should_select_dimensions(intent: dict[str, Any] | None) -> bool:
        if not isinstance(intent, dict) or not intent:
            return True
        intent_type = str(intent.get("intent_type") or "").lower()
        query_shape = intent.get("query_shape") if isinstance(intent.get("query_shape"), dict) else {}
        if bool(query_shape.get("needs_group_by")):
            return True
        if intent_type in {"trend_analysis", "ranking_analysis", "comparison_analysis", "detail_query", "share_analysis"}:
            return True
        dimension_slots = intent.get("dimension_slots")
        if isinstance(dimension_slots, list):
            return any(
                isinstance(slot, dict) and str(slot.get("role") or "").lower() == "group_by"
                for slot in dimension_slots
            )
        return False

    @staticmethod
    def _dimensions_without_filter_only_assets(
        dimensions: list[dict[str, Any]],
        filters: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        filter_dimension_ids = {
            item.get("asset_id")
            for item in filters
            if str(item.get("asset_type") or "").upper() in {"", "DIMENSION"} and item.get("asset_id") is not None
        }
        if not filter_dimension_ids:
            return dimensions
        return [item for item in dimensions if item.get("asset_id") not in filter_dimension_ids]

    def _repair_context(self, ctx: ChatBIRunContext) -> dict[str, Any]:
        """从上一轮 SQL 错误中提取可供重生成 SQL 使用的修复上下文。"""

        sql_error = ctx.sql_error
        repair_plan = sql_error.get("repair_plan") if isinstance(sql_error.get("repair_plan"), dict) else {}
        if not bool(sql_error.get("retryable")) or repair_plan.get("action") != "regenerate_sql":
            return {}
        context = {
            "action": "regenerate_sql",
            "error_code": sql_error.get("error_code"),
            "message": sql_error.get("message"),
            "failed_sql": ctx.sql.get("sql"),
        }
        for key in ("candidate_tables", "candidate_fields"):
            if key in repair_plan:
                context[key] = repair_plan[key]
        return {key: value for key, value in context.items() if value not in (None, "", [])}

    def _reject_same_repair_sql(self, generated_sql: str, repair_context: dict[str, Any]) -> None:
        """避免可修复错误重试时继续提交完全相同的 SQL。"""

        failed_sql = str(repair_context.get("failed_sql") or "")
        if not failed_sql:
            return
        if self._normalize_sql(generated_sql) == self._normalize_sql(failed_sql):
            raise ValueError("SQL_REPAIR_REGENERATED_SAME_SQL")

    @staticmethod
    def _normalize_sql(sql: str) -> str:
        return " ".join(str(sql or "").strip().lower().split())

    def _asset_slots(
        self,
        slot_bindings: dict[str, Any],
        selected_assets: dict[str, Any],
        key: str,
        asset_type: str,
    ) -> list[dict[str, Any]]:
        slots = []
        seen: set[int] = set()
        for item in [*self._items(slot_bindings.get(key)), *self._items(selected_assets.get(key))]:
            asset_id = self._int_or_none(item.get("asset_id"))
            if asset_id is None or asset_id in seen:
                continue
            seen.add(asset_id)
            slots.append(
                {
                    "asset_type": item.get("asset_type") or asset_type,
                    "asset_id": asset_id,
                    "display_name": item.get("display_name") or item.get("name") or item.get("biz_name"),
                    "operator": item.get("operator"),
                    "value": item.get("value"),
                }
            )
        return slots

    @staticmethod
    def _items(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    @staticmethod
    def _used_assets(asset_type: str, biz_names: list[str], elements) -> list[dict[str, Any]]:
        element_by_biz_name = {element.biz_name: element for element in elements}
        assets: list[dict[str, Any]] = []
        for biz_name in biz_names:
            element = element_by_biz_name.get(biz_name)
            if element is None:
                continue
            assets.append(
                {
                    "asset_type": asset_type,
                    "asset_id": element.id,
                    "biz_name": element.biz_name,
                }
            )
        return assets

    def _datasource_id(self, schema, metric_biz_names: list[str], dimension_biz_names: list[str]) -> int | None:
        model_by_id = {model.get("id"): model for model in schema.models}
        for element in [*schema.metrics, *schema.dimensions]:
            if element.biz_name not in {*metric_biz_names, *dimension_biz_names}:
                continue
            model = model_by_id.get(element.model) or {}
            datasource_id = self._int_or_none(model.get("datasource_id") or model.get("datasourceId"))
            if datasource_id is not None:
                return datasource_id
            datasource_id = self._int_or_none(self._measure_datasource_id(element))
            if datasource_id is not None:
                return datasource_id
        return None

    @staticmethod
    def _measure_datasource_id(element) -> Any:
        params = element.type_params or {}
        measure_params = params.get("metricDefineByMeasureParams") if isinstance(params, dict) else {}
        measures = measure_params.get("measures") if isinstance(measure_params, dict) else []
        if measures and isinstance(measures[0], dict):
            return measures[0].get("datasourceId") or measures[0].get("datasource_id")
        return None

    @staticmethod
    def _failed(error_code: str, message: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "rows": [],
            "row_count": 0,
            "fields": [],
            "execution_ms": 0,
            "error_code": error_code,
            "message": message,
        }
