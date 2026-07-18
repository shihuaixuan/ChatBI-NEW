from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from apps.capabilities.sql.executor import SqlExecuteTool
from apps.capabilities.sql.repair import SQLRepairStrategy
from apps.capabilities.sql.validator import SqlValidateTool
from apps.semantic.models.dto import DatasetSchema
from apps.semantic.services.schema_service import DatasetSchemaProvider
from apps.semantic.services.sql_compiler import (
    SemanticSQLCompiler,
    SemanticSQLCompileRequest,
)
from apps.workflow.capabilities import planning
from apps.workflow.capabilities.adapters.permission import PermissionAdapter
from apps.workflow.capabilities.config import ChatBIConfig
from apps.workflow.capabilities.context import ChatBIRunContext
from apps.workflow.capabilities.execution import (
    ExecutionQuery,
    ExecutionResult,
    ResultArtifactStore,
    build_execution_output,
    validate_execution_output,
)


class SqlAdapter:
    """ChatBI v1 SQL 生成适配器，复用 Semantic 语义 SQL 编译器。"""

    def __init__(
        self,
        schema_provider: DatasetSchemaProvider | None = None,
        compiler: SemanticSQLCompiler | None = None,
        execute_tool: SqlExecuteTool | None = None,
        validate_tool: SqlValidateTool | None = None,
        permission_adapter: PermissionAdapter | None = None,
        repair_strategy: SQLRepairStrategy | None = None,
        artifact_store: ResultArtifactStore | None = None,
        sample_row_limit: int | None = None,
        max_parallel_queries: int | None = None,
        config: ChatBIConfig | None = None,
    ) -> None:
        config = config or ChatBIConfig()
        self._schema_provider = schema_provider
        self._compiler = compiler or SemanticSQLCompiler()
        self._execute_tool = execute_tool
        self._validate_tool = validate_tool or SqlValidateTool()
        self._permission_adapter = permission_adapter or PermissionAdapter()
        self._repair_strategy = repair_strategy or SQLRepairStrategy()
        self._artifact_store = artifact_store
        self._sample_row_limit = max(
            sample_row_limit if sample_row_limit is not None else config.sql_sample_row_limit,
            0,
        )
        self._max_parallel_queries = max(
            max_parallel_queries if max_parallel_queries is not None else config.sql_max_parallel_queries,
            1,
        )

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
        question = ctx.question
        dataset_id = ctx.dataset_id
        if not question or dataset_id is None:
            raise ValueError("SQL_GENERATE_CONTEXT_REQUIRED")

        schema = self._build_dataset_schema(ctx.tenant_id, dataset_id)
        plan = ctx.plan
        if plan.get("status") == "infeasible":
            raise ValueError(f"QUERY_PLAN_INFEASIBLE:{plan.get('infeasible_reason') or 'unknown'}")
        if plan.get("status") == "ready":
            # 计划路径：bind_query_plan 已把资产收敛为唯一事实源。
            slots = {
                "metrics": planning.slot_items(plan.get("metrics")),
                "dimensions": planning.slot_items(plan.get("group_bys")),
                "filters": planning.slot_items(plan.get("filters")),
            }
            order_by = planning.slot_items(plan.get("order"))
            having = planning.slot_items(plan.get("having"))
            limit = self._int_or_none(plan.get("limit"))
            time_bucket = plan.get("time") if isinstance(plan.get("time"), dict) and plan.get("time", {}).get("grain") else None
            select_mode = str(plan.get("select_mode") or "aggregate")
        else:
            # 兼容路径：尚未产出计划的旧上下文，直接从知识检索输出推导。
            slots = planning.derive_semantic_slots(ctx.knowledge, ctx.intent)
            order_by, limit = planning.derive_order_and_limit(ctx.intent, slots)
            having = []
            time_bucket = None
            select_mode = "aggregate"
        repair_context = self._repair_context(ctx)
        result = self._compiler.compile(
            SemanticSQLCompileRequest(
                schema=schema,
                question=question,
                slots=slots,
                repair_context=repair_context,
                order_by=order_by,
                limit=limit,
                time_bucket=time_bucket,
                select_mode=select_mode,
                having=having,
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
            "explanation": "基于 Semantic 语义资产生成 SQL",
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
        query = ExecutionQuery(
            query_id="query-0",
            sql=sql,
            datasource_id=datasource_id,
            plan_ref=0,
            metrics=[
                str(item.get("biz_name"))
                for item in sql_info.get("used_assets", [])
                if isinstance(item, dict)
                and item.get("biz_name")
                and item.get("asset_type") == "METRIC"
            ],
            dimensions=[
                str(item.get("biz_name"))
                for item in sql_info.get("used_assets", [])
                if isinstance(item, dict)
                and item.get("biz_name")
                and item.get("asset_type") == "DIMENSION"
            ],
        )
        result = self._execute_query(ctx, query)
        return build_execution_output([query], [result])

    def _execute_query(
        self,
        ctx: ChatBIRunContext,
        query: ExecutionQuery,
    ) -> ExecutionResult:
        if self._execute_tool is None:
            return self._failed_result(
                query.query_id,
                "SQL_EXECUTE_TOOL_REQUIRED",
                "SQL 执行工具未配置",
            )
        permission = self._permission_adapter.apply(
            {
                "sql": query.sql,
                "datasource_id": query.datasource_id,
                "tenant_id": ctx.request_value("tenant_id"),
                "user_id": ctx.request_value("user_id"),
            }
        )
        if not permission.get("allowed"):
            return self._failed_result(
                query.query_id,
                str(permission.get("error_code") or "permission_denied"),
                str(permission.get("reason") or "权限校验拒绝"),
            )
        permitted_sql = str(permission.get("sql") or query.sql)

        result = self._execute_tool.run(
            {"sql": permitted_sql, "datasource_id": query.datasource_id}
        )
        if not result.success:
            return self._failed_result(
                query.query_id,
                result.error_code or "SQL_EXECUTE_FAILED",
                result.message or "SQL 执行失败",
            )
        payload = result.payload or {}
        rows = payload.get("data") or payload.get("rows") or []
        fields = payload.get("fields") or []
        row_count = int(payload.get("row_count") or len(rows))
        sample_rows = rows[: self._sample_row_limit]
        artifact_ref = payload.get("artifact_ref")
        if self._artifact_store is not None:
            try:
                artifact_ref = self._artifact_store.put_json(
                    run_id=ctx.run_id,
                    kind="sql_result",
                    payload={
                        "query_id": query.query_id,
                        "fields": fields,
                        "rows": rows,
                        "row_count": row_count,
                    },
                    metadata={
                        "query_id": query.query_id,
                        "row_count": row_count,
                    },
                )
            except Exception:
                return self._failed_result(
                    query.query_id,
                    "SQL_RESULT_ARTIFACT_WRITE_FAILED",
                    "SQL 结果 Artifact 写入失败",
                )
        return ExecutionResult(
            query_id=query.query_id,
            status="succeeded",
            row_count=row_count,
            fields=fields,
            sample_rows=sample_rows,
            sampled_row_count=len(sample_rows),
            result_truncated=row_count > len(sample_rows),
            artifact_ref=artifact_ref,
            execution_ms=int(payload.get("execution_ms") or 0),
        )

    @staticmethod
    def _failed_result(
        query_id: str,
        error_code: str,
        message: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            query_id=query_id,
            status="failed",
            error_code=error_code,
            message=message,
        )

    def generate_split(self, request: dict[str, Any]) -> dict[str, Any]:
        """分别编译多查询子计划，但不在 SQL 生成节点执行查询。"""
        ctx = ChatBIRunContext(request)
        plans = self._split_plans(ctx)
        dataset_id = ctx.dataset_id
        if dataset_id is None or not isinstance(plans, list) or len(plans) < 2:
            raise ValueError("CROSS_MODEL_PLAN_REQUIRED")

        schema = self._build_dataset_schema(ctx.tenant_id, dataset_id)
        queries: list[dict[str, Any]] = []
        for index, plan in enumerate(plans):
            slots = plan.get("slots") if isinstance(plan.get("slots"), dict) else {}
            compiled = self._compiler.compile(
                SemanticSQLCompileRequest(
                    schema=schema,
                    question=ctx.raw_question,
                    slots=slots,
                    select_mode=str(plan.get("select_mode") or "aggregate"),
                    having=planning.slot_items(slots.get("having") or plan.get("having")),
                )
            )
            validated = self._validate_tool.run({"sql": compiled.sql, "allowed_tables": compiled.tables})
            if not validated.success:
                raise ValueError(validated.error_code or "SQL_VALIDATE_FAILED")
            sql = (validated.payload or {}).get("sql") or compiled.sql
            datasource_id = self._datasource_id(schema, compiled.metrics, compiled.dimensions)
            queries.append(
                {
                    "plan_ref": index,
                    "role": plan.get("role"),
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

    @staticmethod
    def _split_plans(ctx: ChatBIRunContext) -> list[dict[str, Any]] | Any:
        plan = ctx.plan
        sub_plans = plan.get("sub_plans")
        if plan.get("strategy") == "multi_query" and isinstance(sub_plans, list):
            return sub_plans
        return ctx.knowledge.get("multi_query_plans")

    def _build_dataset_schema(self, tenant_id: int, dataset_id: int) -> DatasetSchema:
        if self._schema_provider is None:
            raise ValueError("SEMANTIC_SCHEMA_PROVIDER_REQUIRED")
        return self._schema_provider.build_dataset_schema(tenant_id, dataset_id)

    def execute_split(self, request: dict[str, Any]) -> dict[str, Any]:
        """并行执行已生成的跨模型 SQL，不承担 SQL 编译职责。"""

        ctx = ChatBIRunContext(request)
        raw_queries = ctx.split_sql.get("queries")
        if not isinstance(raw_queries, list) or len(raw_queries) < 2:
            return self._failed("CROSS_MODEL_SQL_REQUIRED", "缺少已生成的跨模型 SQL")
        queries: list[ExecutionQuery] = []
        for index, raw_query in enumerate(raw_queries):
            sql = str(raw_query.get("sql") or "").strip()
            datasource_id = self._int_or_none(raw_query.get("datasource_id"))
            if not sql or datasource_id is None:
                return self._failed("CROSS_MODEL_SQL_INVALID", "跨模型 SQL 缺少 SQL 或 datasource_id")
            queries.append(
                ExecutionQuery(
                    query_id=f"query-{index}",
                    sql=sql,
                    datasource_id=datasource_id,
                    plan_ref=index,
                    role=str(raw_query.get("role") or "") or None,
                    model_id=self._int_or_none(raw_query.get("model_id")),
                    metrics=[
                        str(item)
                        for item in raw_query.get("metrics") or []
                        if item is not None
                    ],
                    dimensions=[
                        str(item)
                        for item in raw_query.get("dimensions") or []
                        if item is not None
                    ],
                )
            )
        with ThreadPoolExecutor(
            max_workers=min(len(queries), self._max_parallel_queries)
        ) as executor:
            futures = [
                executor.submit(self._execute_query, ctx, query)
                for query in queries
            ]
            results = [
                self._completed_future_result(query, future)
                for query, future in zip(queries, futures, strict=True)
            ]
        return build_execution_output(queries, results)

    def _completed_future_result(self, query, future) -> ExecutionResult:
        try:
            return future.result()
        except Exception:
            return self._failed_result(
                query.query_id,
                "SQL_EXECUTE_UNEXPECTED_ERROR",
                "拆分查询执行发生未预期错误",
            )

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

    def validate_result(self, request: dict[str, Any]) -> dict[str, Any]:
        """校验执行结果，产出可供路由和回答投影使用的稳定结论。"""

        return validate_execution_output(ChatBIRunContext(request).execution)

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
