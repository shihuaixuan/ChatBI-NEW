from __future__ import annotations

from typing import Any

from apps.agentic_chat.strategies.sql_repair import SQLRepairStrategy
from apps.agentic_chat.tools.sql_executor import SqlExecuteTool
from apps.agentic_chat.tools.sql_validator import SqlValidateTool
from apps.chatbi_workflow.capabilities.adapters.permission import PermissionAdapter
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
        raw_request = request.get("request", {})
        variables = request.get("variables", {})
        if not isinstance(variables, dict):
            variables = {}
        rewrite = variables.get("rewrite") if isinstance(variables.get("rewrite"), dict) else {}
        knowledge = variables.get("knowledge") if isinstance(variables.get("knowledge"), dict) else {}
        question = str(rewrite.get("rewritten_question") or raw_request.get("question") or "").strip()
        dataset_id = raw_request.get("dataset_id")
        oid = raw_request.get("tenant_id") or raw_request.get("oid") or 1
        if not question or dataset_id is None:
            raise ValueError("SQL_GENERATE_CONTEXT_REQUIRED")

        schema = self._schema_builder.build_dataset_schema(int(oid), int(dataset_id))
        slots = self._compile_slots(knowledge)
        repair_context = self._repair_context(variables)
        result = self._compiler.compile(
            SemanticSQLCompileRequest(
                schema=schema,
                question=question,
                slots=slots,
                repair_context=repair_context,
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
        variables = request.get("variables", {})
        sql_info = variables.get("sql") if isinstance(variables, dict) and isinstance(variables.get("sql"), dict) else {}
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
                "tenant_id": request.get("request", {}).get("tenant_id"),
                "user_id": request.get("request", {}).get("user_id"),
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

    def handle_error(self, request: dict[str, Any]) -> dict[str, Any]:
        variables = request.get("variables", {})
        execution = (
            variables.get("sql_execution")
            if isinstance(variables, dict) and isinstance(variables.get("sql_execution"), dict)
            else {}
        )
        error_code = str(execution.get("error_code") or "SQL_EXECUTION_FAILED")
        raw_message = str(execution.get("message") or "SQL 执行失败")
        sql_info = variables.get("sql") if isinstance(variables.get("sql"), dict) else {}
        knowledge = variables.get("knowledge") if isinstance(variables.get("knowledge"), dict) else {}
        repair = self._repair_strategy.decide(
            error_code=error_code,
            message=raw_message,
            sql=str(sql_info.get("sql") or ""),
            knowledge=knowledge,
        )
        return {
            "error_code": error_code,
            "message": f"SQL 执行失败：{raw_message}",
            "retryable": repair.retryable,
            "repair_hint": repair.repair_hint,
            "repair_plan": repair.plan(),
        }

    def _compile_slots(self, knowledge: dict[str, Any]) -> dict[str, Any]:
        slot_bindings = knowledge.get("slot_bindings") if isinstance(knowledge.get("slot_bindings"), dict) else {}
        selected_assets = knowledge.get("selected_assets") if isinstance(knowledge.get("selected_assets"), dict) else {}
        return {
            "metrics": self._asset_slots(slot_bindings, selected_assets, "metrics", "METRIC"),
            "dimensions": self._asset_slots(slot_bindings, selected_assets, "dimensions", "DIMENSION"),
            "filters": self._asset_slots(slot_bindings, selected_assets, "filters", "DIMENSION"),
        }

    def _repair_context(self, variables: dict[str, Any]) -> dict[str, Any]:
        """从上一轮 SQL 错误中提取可供重生成 SQL 使用的修复上下文。"""

        sql_error = variables.get("sql_error") if isinstance(variables.get("sql_error"), dict) else {}
        repair_plan = sql_error.get("repair_plan") if isinstance(sql_error.get("repair_plan"), dict) else {}
        if not bool(sql_error.get("retryable")) or repair_plan.get("action") != "regenerate_sql":
            return {}
        sql_info = variables.get("sql") if isinstance(variables.get("sql"), dict) else {}
        context = {
            "action": "regenerate_sql",
            "error_code": sql_error.get("error_code"),
            "message": sql_error.get("message"),
            "failed_sql": sql_info.get("sql"),
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
