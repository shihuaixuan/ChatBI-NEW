import threading

import pytest

from apps.capabilities.schemas import ToolResult
from apps.semantic.models.dto import DatasetSchema, SchemaElement
from apps.semantic.services.sql_compiler import SemanticSQLCompileResult
from apps.workflow.capabilities.adapters.sql import SqlAdapter
from apps.workflow.capabilities.config import ChatBIConfig


class FakeDatasetSchemaProvider:
    def __init__(self, schema: DatasetSchema) -> None:
        self.schema = schema
        self.calls: list[tuple[int, int]] = []

    def build_dataset_schema(self, oid: int, dataset_id: int) -> DatasetSchema:
        self.calls.append((oid, dataset_id))
        return self.schema


def test_sql_adapter_generates_sql_from_semantic_selected_assets():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="档口经营分析",
            id=20,
            name="档口经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[
            {
                "id": 10,
                "name": "档口流量模型",
                "biz_name": "stall_traffic",
                "datasource_id": 5,
                "tableQuery": "stall_traffic_1d",
                "dimensions": [{"name": "统计日期", "bizName": "stat_date", "expr": "stat_date"}],
                "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
            }
        ],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=100,
                name="访问人数",
                biz_name="visit_uv",
                type="METRIC",
                default_agg="SUM",
                fields=["visit_uv"],
            )
        ],
        dimensions=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=200,
                name="统计日期",
                biz_name="stat_date",
                type="DIMENSION",
            )
        ],
    )
    schema_provider = FakeDatasetSchemaProvider(schema)
    adapter = SqlAdapter(schema_provider=schema_provider)

    result = adapter.generate(
        {
            "request": {"question": "按日期看访问人数", "dataset_id": 20, "tenant_id": 10},
            "variables": {
                "rewrite": {"rewritten_question": "按日期看访问人数"},
                "knowledge": {
                    "slot_bindings": {
                        "metrics": [{"asset_type": "METRIC", "asset_id": 100, "display_name": "访问人数"}],
                        "dimensions": [{"asset_type": "DIMENSION", "asset_id": 200, "display_name": "统计日期"}],
                    },
                    "selected_assets": {
                        "metrics": [{"asset_id": 100, "biz_name": "visit_uv", "display_name": "访问人数"}],
                        "dimensions": [{"asset_id": 200, "biz_name": "stat_date", "display_name": "统计日期"}],
                    },
                },
            },
        }
    )

    assert schema_provider.calls == [(10, 20)]
    assert result == {
        "sql": (
            "select stall_traffic.stat_date as stat_date, sum(stall_traffic.visit_uv) as visit_uv "
            "from stall_traffic_1d stall_traffic "
            "group by stall_traffic.stat_date limit 100"
        ),
        "strategy": "semantic_sql_compiler",
        "datasource_id": 5,
        "explanation": "基于 Semantic 语义资产生成 SQL",
        "used_assets": [
            {"asset_type": "METRIC", "asset_id": 100, "biz_name": "visit_uv"},
            {"asset_type": "DIMENSION", "asset_id": 200, "biz_name": "stat_date"},
        ],
    }


def test_sql_adapter_treats_filter_dimensions_as_where_conditions_only():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="档口经营分析",
            id=20,
            name="档口经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[
            {
                "id": 10,
                "name": "档口流量模型",
                "biz_name": "stall_traffic",
                "datasource_id": 5,
                "tableQuery": "stall_traffic_1d",
                "dimensions": [
                    {"name": "档口", "bizName": "stall_id", "expr": "stall_id"},
                    {"name": "统计日期", "bizName": "stat_date", "expr": "stat_date"},
                ],
                "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
            }
        ],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=100,
                name="访问人数",
                biz_name="visit_uv",
                type="METRIC",
                default_agg="NONE",
                fields=["visit_uv"],
            )
        ],
        dimensions=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=200,
                name="档口",
                biz_name="stall_id",
                type="DIMENSION",
            ),
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=201,
                name="统计日期",
                biz_name="stat_date",
                type="DIMENSION",
            ),
        ],
    )
    adapter = SqlAdapter(schema_provider=FakeDatasetSchemaProvider(schema))

    result = adapter.generate(
        {
            "request": {"question": "今天档口1的访问人数", "dataset_id": 20, "tenant_id": 10},
            "variables": {
                "rewrite": {"rewritten_question": "今天档口1的访问人数"},
                "knowledge": {
                    "slot_bindings": {
                        "metrics": [{"asset_type": "METRIC", "asset_id": 100, "display_name": "访问人数"}],
                        "dimensions": [
                            {"asset_type": "DIMENSION", "asset_id": 200, "display_name": "档口"},
                            {"asset_type": "DIMENSION", "asset_id": 201, "display_name": "统计日期"},
                        ],
                        "filters": [
                            {
                                "asset_type": "DIMENSION",
                                "asset_id": 200,
                                "display_name": "档口",
                                "operator": "=",
                                "value": "1",
                            },
                            {
                                "asset_type": "DIMENSION",
                                "asset_id": 201,
                                "display_name": "统计日期",
                                "operator": "=",
                                "value": {"kind": "relative_date", "value": "today"},
                            },
                        ],
                    },
                    "selected_assets": {
                        "metrics": [{"asset_id": 100, "biz_name": "visit_uv", "display_name": "访问人数"}],
                        "dimensions": [
                            {"asset_id": 200, "biz_name": "stall_id", "display_name": "档口"},
                            {"asset_id": 201, "biz_name": "stat_date", "display_name": "统计日期"},
                        ],
                    },
                },
            },
        }
    )

    assert result["sql"] == (
        "select stall_traffic.visit_uv as visit_uv "
        "from stall_traffic_1d stall_traffic "
        "where stall_traffic.stall_id = '1' and stall_traffic.stat_date = CURRENT_DATE limit 100"
    )


def test_sql_adapter_does_not_select_dimension_mentions_for_plain_metric_query():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="店铺经营分析",
            id=20,
            name="店铺经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[
            {
                "id": 10,
                "name": "客户模型",
                "biz_name": "customer_model",
                "datasource_id": 5,
                "tableQuery": "customer_daily",
            }
        ],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="店铺经营分析",
                model=10,
                id=100,
                name="总客户数-线上（累计）",
                biz_name="total_customer_cnt_online",
                type="METRIC",
            )
        ],
        dimensions=[
            SchemaElement(
                data_set_id=20,
                data_set_name="店铺经营分析",
                model=10,
                id=200,
                name="店铺ID",
                biz_name="stall_id",
                type="DIMENSION",
            ),
            SchemaElement(
                data_set_id=20,
                data_set_name="店铺经营分析",
                model=10,
                id=201,
                name="时间",
                biz_name="stat_date",
                type="DIMENSION",
            ),
            SchemaElement(
                data_set_id=20,
                data_set_name="店铺经营分析",
                model=10,
                id=202,
                name="Top 10 贡献客户",
                biz_name="top10_contrib_customers",
                type="DIMENSION",
            ),
        ],
    )
    compiler = CapturingCompiler(
        "select stall_traffic.total_customer_cnt_online as total_customer_cnt_online from stall_traffic_1d stall_traffic"
    )
    adapter = SqlAdapter(schema_provider=FakeDatasetSchemaProvider(schema), compiler=compiler)

    adapter.generate(
        {
            "request": {"question": "今天店铺1的线上客户数", "dataset_id": 20, "tenant_id": 10},
            "variables": {
                "intent": {
                    "intent_type": "metric_query",
                    "query_shape": {"needs_group_by": False},
                    "dimension_slots": [{"name": "店铺", "role": "filter", "value": "1", "value_status": "provided"}],
                },
                "knowledge": {
                    "slot_bindings": {
                        "metrics": [{"asset_type": "METRIC", "asset_id": 100, "display_name": "总客户数-线上（累计）"}],
                        "dimensions": [
                            {"asset_type": "DIMENSION", "asset_id": 200, "display_name": "店铺ID"},
                            {"asset_type": "DIMENSION", "asset_id": 202, "display_name": "Top 10 贡献客户"},
                            {"asset_type": "DIMENSION", "asset_id": 201, "display_name": "时间"},
                        ],
                        "filters": [
                            {"asset_type": "DIMENSION", "asset_id": 200, "display_name": "店铺ID", "operator": "=", "value": "1"},
                            {
                                "asset_type": "DIMENSION",
                                "asset_id": 201,
                                "display_name": "时间",
                                "operator": "=",
                                "value": {"kind": "relative_date", "value": "today"},
                            },
                        ],
                    },
                    "selected_assets": {
                        "metrics": [{"asset_id": 100, "biz_name": "total_customer_cnt_online", "display_name": "总客户数-线上（累计）"}],
                        "dimensions": [
                            {"asset_id": 200, "biz_name": "stall_id", "display_name": "店铺ID"},
                            {"asset_id": 202, "biz_name": "top10_contrib_customers", "display_name": "Top 10 贡献客户"},
                            {"asset_id": 201, "biz_name": "stat_date", "display_name": "时间"},
                        ],
                    },
                },
            },
        }
    )

    assert compiler.requests[0].slots["metrics"] == [
        {"asset_type": "METRIC", "asset_id": 100, "display_name": "总客户数-线上（累计）", "operator": None, "value": None}
    ]
    assert compiler.requests[0].slots["dimensions"] == []
    assert [item["asset_id"] for item in compiler.requests[0].slots["filters"]] == [200, 201]


class UnsafeCompiler:
    def compile(self, request):
        return SemanticSQLCompileResult(
            sql="delete from stall_traffic_1d",
            tables=["stall_traffic_1d"],
            metrics=["visit_uv"],
            dimensions=[],
        )


class CapturingCompiler:
    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.requests = []

    def compile(self, request):
        self.requests.append(request)
        return SemanticSQLCompileResult(
            sql=self.sql,
            tables=["stall_traffic_1d"],
            metrics=["visit_uv"],
            dimensions=[],
        )


def test_sql_adapter_uses_separated_group_dimensions_and_time_filters():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="店铺经营分析",
            id=20,
            name="店铺经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "店铺订单", "biz_name": "stall_order", "tableQuery": "stall_traffic_1d"}],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="店铺经营分析",
                model=10,
                id=100,
                name="总GMV",
                biz_name="gmv_total",
                type="METRIC",
            )
        ],
        dimensions=[
            SchemaElement(
                data_set_id=20,
                data_set_name="店铺经营分析",
                model=10,
                id=200,
                name="店铺ID",
                biz_name="stall_id",
                type="DIMENSION",
            ),
            SchemaElement(
                data_set_id=20,
                data_set_name="店铺经营分析",
                model=10,
                id=201,
                name="统计日期",
                biz_name="stat_date",
                type="DIMENSION",
            ),
        ],
    )
    compiler = CapturingCompiler(
        "select stall_order.stall_id, sum(stall_order.gmv_total) as gmv_total "
        "from stall_traffic_1d stall_order group by stall_order.stall_id"
    )
    adapter = SqlAdapter(schema_provider=FakeDatasetSchemaProvider(schema), compiler=compiler)

    adapter.generate(
        {
            "request": {"question": "最近 30 天按店铺看总GMV", "dataset_id": 20, "tenant_id": 10},
            "variables": {
                "intent": {
                    "intent_type": "metric_query",
                    "dimension_slots": [{"name": "店铺", "role": "group_by", "value": None, "value_status": "not_provided"}],
                },
                "knowledge": {
                    "slot_bindings": {
                        "metrics": [{"asset_type": "METRIC", "asset_id": 100, "display_name": "总GMV"}],
                        "group_dimensions": [{"asset_type": "DIMENSION", "asset_id": 200, "display_name": "店铺ID"}],
                        "time_filters": [
                            {
                                "asset_type": "DIMENSION",
                                "asset_id": 201,
                                "display_name": "统计日期",
                                "operator": "=",
                                "value": {"kind": "relative_range", "amount": 30, "unit": "day"},
                            }
                        ],
                    },
                    "selected_assets": {
                        "metrics": [{"asset_id": 100, "biz_name": "gmv_total", "display_name": "总GMV"}],
                        "business_dimensions": [{"asset_id": 200, "biz_name": "stall_id", "display_name": "店铺ID"}],
                        "time_dimensions": [{"asset_id": 201, "biz_name": "stat_date", "display_name": "统计日期"}],
                    },
                },
            },
        }
    )

    assert [item["asset_id"] for item in compiler.requests[0].slots["dimensions"]] == [200]
    assert [item["asset_id"] for item in compiler.requests[0].slots["filters"]] == [201]


def test_sql_adapter_passes_ranking_order_and_limit_to_compiler():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="档口经营分析",
            id=20,
            name="档口经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "档口订单", "biz_name": "stall_order", "tableQuery": "fct_stall_order_daily"}],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=100,
                name="销售GMV",
                biz_name="gmv_sale",
                type="METRIC",
            )
        ],
        dimensions=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=200,
                name="档口",
                biz_name="stall_id",
                type="DIMENSION",
            )
        ],
    )
    compiler = CapturingCompiler(
        "select stall_order.stall_id, sum(stall_order.gmv_sale) as gmv_sale "
        "from stall_traffic_1d stall_order group by stall_order.stall_id "
        "order by gmv_sale desc limit 5"
    )
    adapter = SqlAdapter(schema_provider=FakeDatasetSchemaProvider(schema), compiler=compiler)

    adapter.generate(
        {
            "request": {"question": "销售GMV最高的5个档口", "dataset_id": 20, "tenant_id": 10},
            "variables": {
                "intent": {
                    "intent_type": "ranking_analysis",
                    "query_shape": {
                        "needs_group_by": True,
                        "needs_order_by": True,
                        "order_direction": "desc",
                        "limit": 5,
                    },
                },
                "knowledge": {
                    "slot_bindings": {
                        "metrics": [{"asset_type": "METRIC", "asset_id": 100}],
                        "dimensions": [{"asset_type": "DIMENSION", "asset_id": 200}],
                    }
                },
            },
        }
    )

    assert compiler.requests[0].order_by == [
        {"asset_type": "METRIC", "asset_id": 100, "direction": "desc"}
    ]
    assert compiler.requests[0].limit == 5


def test_sql_adapter_rejects_unsafe_generated_sql():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="档口经营分析",
            id=20,
            name="档口经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "档口流量模型", "biz_name": "stall_traffic", "tableQuery": "stall_traffic_1d"}],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=100,
                name="访问人数",
                biz_name="visit_uv",
                type="METRIC",
            )
        ],
        dimensions=[],
    )
    adapter = SqlAdapter(schema_provider=FakeDatasetSchemaProvider(schema), compiler=UnsafeCompiler())

    with pytest.raises(ValueError, match="unsafe_statement"):
        adapter.generate(
            {
                "request": {"question": "删除访问人数", "dataset_id": 20, "tenant_id": 10},
                "variables": {"knowledge": {"slot_bindings": {"metrics": [{"asset_type": "METRIC", "asset_id": 100}]}}},
            }
        )


def test_sql_adapter_passes_repair_context_to_compiler_on_retry():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="档口经营分析",
            id=20,
            name="档口经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[
            {
                "id": 10,
                "name": "档口流量模型",
                "biz_name": "stall_traffic",
                "datasource_id": 5,
                "tableQuery": "stall_traffic_1d",
                "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
            }
        ],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=100,
                name="访问人数",
                biz_name="visit_uv",
                type="METRIC",
                default_agg="SUM",
                fields=["visit_uv"],
            )
        ],
        dimensions=[],
    )
    compiler = CapturingCompiler("select sum(stall_traffic.visit_uv) as visit_uv from stall_traffic_1d stall_traffic")
    adapter = SqlAdapter(schema_provider=FakeDatasetSchemaProvider(schema), compiler=compiler)

    adapter.generate(
        {
            "request": {"question": "今日访问人数", "dataset_id": 20, "tenant_id": 10},
            "variables": {
                "knowledge": {
                    "slot_bindings": {"metrics": [{"asset_type": "METRIC", "asset_id": 100, "display_name": "访问人数"}]},
                    "tables": ["stall_traffic_1d"],
                },
                "sql": {"sql": "select * from missing_table", "datasource_id": 5},
                "sql_error": {
                    "error_code": "sql_execute_error",
                    "message": "SQL 执行失败：Table 'missing_table' doesn't exist",
                    "retryable": True,
                    "repair_plan": {
                        "action": "regenerate_sql",
                        "reason": "SQL 引用了不存在的表",
                        "retryable": True,
                        "candidate_tables": ["stall_traffic_1d"],
                    },
                },
            },
        }
    )

    assert compiler.requests[0].repair_context == {
        "action": "regenerate_sql",
        "error_code": "sql_execute_error",
        "message": "SQL 执行失败：Table 'missing_table' doesn't exist",
        "failed_sql": "select * from missing_table",
        "candidate_tables": ["stall_traffic_1d"],
    }


def test_sql_adapter_rejects_retry_when_regenerated_sql_is_same_as_failed_sql():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="档口经营分析",
            id=20,
            name="档口经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "档口流量模型", "biz_name": "stall_traffic", "tableQuery": "missing_table"}],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=100,
                name="访问人数",
                biz_name="visit_uv",
                type="METRIC",
            )
        ],
        dimensions=[],
    )
    failed_sql = "select sum(visit_uv) as visit_uv from missing_table"
    adapter = SqlAdapter(schema_provider=FakeDatasetSchemaProvider(schema), compiler=CapturingCompiler(failed_sql))

    with pytest.raises(ValueError, match="SQL_REPAIR_REGENERATED_SAME_SQL"):
        adapter.generate(
            {
                "request": {"question": "今日访问人数", "dataset_id": 20, "tenant_id": 10},
                "variables": {
                    "knowledge": {
                        "slot_bindings": {
                            "metrics": [{"asset_type": "METRIC", "asset_id": 100, "display_name": "访问人数"}]
                        }
                    },
                    "sql": {"sql": failed_sql, "datasource_id": 5},
                    "sql_error": {
                        "error_code": "sql_execute_error",
                        "message": "SQL 执行失败：Table 'missing_table' doesn't exist",
                        "retryable": True,
                        "repair_plan": {"action": "regenerate_sql", "retryable": True},
                    },
                },
            }
        )


class FakeSqlExecuteTool:
    def __init__(self, result: ToolResult) -> None:
        self.result = result
        self.payloads: list[dict] = []

    def run(self, payload: dict) -> ToolResult:
        self.payloads.append(payload)
        return self.result


class FakeResultArtifactStore:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def put_json(self, **payload):
        if self.fail:
            raise RuntimeError("artifact unavailable")
        self.calls.append(payload)
        return {
            "artifact_id": "artifact-1",
            "kind": "sql_result",
            "content_type": "application/json",
            "size": 10,
            "digest": "sha256:test",
            "metadata": payload.get("metadata") or {},
        }


class BlockingSqlExecuteTool:
    def __init__(self) -> None:
        self.barrier = threading.Barrier(2)
        self.lock = threading.Lock()
        self.active_calls = 0
        self.max_active_calls = 0

    def run(self, payload: dict) -> ToolResult:
        with self.lock:
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
        self.barrier.wait(timeout=2)
        with self.lock:
            self.active_calls -= 1
        value = 1 if "first" in payload["sql"] else 2
        return ToolResult(
            success=True,
            payload={"fields": ["value"], "data": [{"value": value}]},
        )


class SelectiveFailureSqlExecuteTool:
    def run(self, payload: dict) -> ToolResult:
        if "failed" in payload["sql"]:
            return ToolResult(
                success=False,
                error_code="sql_execute_error",
                message="query failed",
            )
        return ToolResult(
            success=True,
            payload={"fields": ["value"], "data": [{"value": 1}]},
        )


class DenyPermissionAdapter:
    def apply(self, payload: dict) -> dict:
        return {"allowed": False, "reason": "没有数据源权限", "sql": None, "error_code": "permission_denied"}


def test_sql_adapter_executes_sql_and_normalizes_result_rows():
    execute_tool = FakeSqlExecuteTool(
        ToolResult(
            success=True,
            payload={
                "fields": ["visit_uv"],
                "data": [{"visit_uv": 123}],
            },
        )
    )
    adapter = SqlAdapter(execute_tool=execute_tool)

    result = adapter.execute(
        {
            "variables": {
                "sql": {
                    "sql": "select sum(visit_uv) as visit_uv from stall_traffic_1d",
                    "datasource_id": 5,
                }
            }
        }
    )

    assert execute_tool.payloads == [
        {
            "sql": (
                "select sum(visit_uv) as visit_uv "
                "from stall_traffic_1d limit 100"
            ),
            "datasource_id": 5,
        }
    ]
    assert result["status"] == "succeeded"
    assert result["rows"] == [{"visit_uv": 123}]
    assert result["row_count"] == 1
    assert result["fields"] == ["visit_uv"]
    assert result["queries"][0]["query_id"] == "query-0"
    assert result["results"][0]["sample_rows"] == [{"visit_uv": 123}]


def test_sql_adapter_executes_single_query_with_uniform_result_and_artifact():
    execute_tool = FakeSqlExecuteTool(
        ToolResult(
            success=True,
            payload={
                "fields": ["value"],
                "data": [{"value": 1}, {"value": 2}],
                "execution_ms": 4,
            },
        )
    )
    artifact_store = FakeResultArtifactStore()
    adapter = SqlAdapter(
        execute_tool=execute_tool,
        artifact_store=artifact_store,
        sample_row_limit=1,
    )

    result = adapter.execute(
        {
            "run_id": "run-1",
            "variables": {
                "sql": {
                    "sql": "select value from t",
                    "datasource_id": 5,
                }
            },
        }
    )

    assert result["queries"][0]["query_id"] == "query-0"
    assert result["results"][0]["sample_rows"] == [{"value": 1}]
    assert result["results"][0]["artifact_ref"]["artifact_id"] == "artifact-1"
    assert artifact_store.calls[0]["payload"]["rows"] == [
        {"value": 1},
        {"value": 2},
    ]
    assert result["rows"] == [{"value": 1}]


def test_sql_adapter_returns_stable_failure_when_artifact_write_fails():
    execute_tool = FakeSqlExecuteTool(
        ToolResult(success=True, payload={"fields": ["value"], "data": [{"value": 1}]})
    )
    adapter = SqlAdapter(
        execute_tool=execute_tool,
        artifact_store=FakeResultArtifactStore(fail=True),
    )

    result = adapter.execute(
        {
            "run_id": "run-1",
            "variables": {
                "sql": {
                    "sql": "select value from t",
                    "datasource_id": 5,
                }
            },
        }
    )

    assert result["status"] == "failed"
    assert result["results"][0]["error_code"] == "SQL_RESULT_ARTIFACT_WRITE_FAILED"


def test_sql_adapter_executes_cross_model_plans_as_independent_queries():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        models=[
            {
                "id": 10,
                "name": "订单模型",
                "biz_name": "orders",
                "datasource_id": 5,
                "tableQuery": "orders_daily",
                "measures": [{"name": "总GMV", "bizName": "gmv_total", "expr": "gmv_total", "agg": "SUM"}],
            },
            {
                "id": 11,
                "name": "库存模型",
                "biz_name": "inventory",
                "datasource_id": 5,
                "tableQuery": "inventory_snapshot",
                "measures": [{"name": "库存量", "bizName": "stock_qty", "expr": "stock_qty", "agg": "SUM"}],
            },
        ],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=100,
                name="总GMV",
                biz_name="gmv_total",
                type="METRIC",
                default_agg="SUM",
            ),
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=11,
                id=101,
                name="库存量",
                biz_name="stock_qty",
                type="METRIC",
                default_agg="SUM",
            ),
        ],
    )
    execute_tool = FakeSqlExecuteTool(
        ToolResult(success=True, payload={"fields": ["value"], "data": [{"value": 10}]})
    )
    adapter = SqlAdapter(
        schema_provider=FakeDatasetSchemaProvider(schema),
        execute_tool=execute_tool,
    )

    generated = adapter.generate_split(
        {
            "request": {"question": "总GMV和库存量", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {
                "knowledge": {
                    "multi_query_plans": [
                        {
                            "model_id": 10,
                            "metrics": ["总GMV"],
                            "dimensions": [],
                            "slots": {"metrics": [{"asset_type": "METRIC", "asset_id": 100}]},
                        },
                        {
                            "model_id": 11,
                            "metrics": ["库存量"],
                            "dimensions": [],
                            "slots": {"metrics": [{"asset_type": "METRIC", "asset_id": 101}]},
                        },
                    ]
                }
            },
        }
    )
    assert len(generated["queries"]) == 2
    assert "gmv_total" in generated["queries"][0]["sql"]
    assert "stock_qty" in generated["queries"][1]["sql"]
    assert execute_tool.payloads == []

    result = adapter.execute_split(
        {
            "request": {"question": "总GMV和库存量", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {"split_sql": generated},
        }
    )

    assert result["status"] == "succeeded"
    assert result["rows"] == []
    assert len(result["results"]) == 2
    assert result["queries"][0]["model_id"] == 10
    assert result["queries"][1]["model_id"] == 11
    assert len(execute_tool.payloads) == 2


def test_sql_adapter_executes_split_queries_in_parallel_and_keeps_order():
    execute_tool = BlockingSqlExecuteTool()
    adapter = SqlAdapter(
        execute_tool=execute_tool,
        artifact_store=FakeResultArtifactStore(),
    )

    result = adapter.execute_split(
        {
            "run_id": "run-1",
            "variables": {
                "split_sql": {
                    "queries": [
                        {
                            "sql": "select first",
                            "datasource_id": 5,
                            "model_id": 10,
                        },
                        {
                            "sql": "select second",
                            "datasource_id": 5,
                            "model_id": 11,
                        },
                    ]
                }
            },
        }
    )

    assert execute_tool.max_active_calls == 2
    assert [item["query_id"] for item in result["results"]] == [
        "query-0",
        "query-1",
    ]
    assert [item["sample_rows"][0]["value"] for item in result["results"]] == [
        1,
        2,
    ]


def test_sql_adapter_preserves_successful_split_result_when_sibling_fails():
    adapter = SqlAdapter(
        execute_tool=SelectiveFailureSqlExecuteTool(),
        artifact_store=FakeResultArtifactStore(),
    )

    result = adapter.execute_split(
        {
            "run_id": "run-1",
            "variables": {
                "split_sql": {
                    "queries": [
                        {"sql": "select first", "datasource_id": 5},
                        {"sql": "select failed", "datasource_id": 5},
                    ]
                }
            },
        }
    )

    assert result["status"] == "failed"
    assert [item["status"] for item in result["results"]] == [
        "succeeded",
        "failed",
    ]
    assert result["error_code"] == "sql_execute_error"


def test_sql_adapter_keeps_only_sample_rows_for_large_result():
    execute_tool = FakeSqlExecuteTool(
        ToolResult(
            success=True,
            payload={
                "fields": ["visit_uv"],
                "data": [{"visit_uv": 1}, {"visit_uv": 2}, {"visit_uv": 3}],
            },
        )
    )
    adapter = SqlAdapter(execute_tool=execute_tool, sample_row_limit=2)

    result = adapter.execute(
        {
            "variables": {
                "sql": {
                    "sql": "select visit_uv from stall_traffic_1d",
                    "datasource_id": 5,
                }
            }
        }
    )

    assert result["row_count"] == 3
    assert result["rows"] == [{"visit_uv": 1}, {"visit_uv": 2}]
    assert result["sampled_row_count"] == 2
    assert result["result_truncated"] is True
    assert result["artifact_ref"] is None


def test_sql_adapter_uses_chatbi_config_sample_row_limit():
    execute_tool = FakeSqlExecuteTool(
        ToolResult(
            success=True,
            payload={
                "fields": ["visit_uv"],
                "data": [{"visit_uv": 1}, {"visit_uv": 2}, {"visit_uv": 3}],
            },
        )
    )
    adapter = SqlAdapter(
        execute_tool=execute_tool,
        config=ChatBIConfig(sql_sample_row_limit=1),
    )

    result = adapter.execute(
        {
            "variables": {
                "sql": {
                    "sql": "select visit_uv from stall_traffic_1d",
                    "datasource_id": 5,
                }
            }
        }
    )

    assert result["rows"] == [{"visit_uv": 1}]
    assert result["sampled_row_count"] == 1


def test_sql_adapter_returns_failed_result_when_execute_tool_fails():
    execute_tool = FakeSqlExecuteTool(
        ToolResult(success=False, error_code="sql_execute_error", message="table not found")
    )
    adapter = SqlAdapter(execute_tool=execute_tool)

    result = adapter.execute(
        {
            "variables": {
                "sql": {
                    "sql": "select * from missing_table",
                    "datasource_id": 5,
                }
            }
        }
    )

    assert result["status"] == "failed"
    assert result["rows"] == []
    assert result["error_code"] == "sql_execute_error"
    assert result["message"] == "table not found"
    assert result["results"][0]["status"] == "failed"


def test_sql_adapter_does_not_execute_sql_when_permission_denied():
    execute_tool = FakeSqlExecuteTool(ToolResult(success=True, payload={"fields": [], "data": []}))
    adapter = SqlAdapter(execute_tool=execute_tool, permission_adapter=DenyPermissionAdapter())

    result = adapter.execute(
        {
            "variables": {
                "sql": {
                    "sql": "select * from orders",
                    "datasource_id": 5,
                }
            }
        }
    )

    assert execute_tool.payloads == []
    assert result["status"] == "failed"
    assert result["rows"] == []
    assert result["error_code"] == "permission_denied"
    assert result["message"] == "没有数据源权限"
    assert result["results"][0]["status"] == "failed"


def test_sql_adapter_handles_sql_execution_error_with_repair_hint():
    adapter = SqlAdapter()

    result = adapter.handle_error(
        {
            "variables": {
                "sql": {"sql": "select * from missing_table"},
                "sql_execution": {
                    "status": "failed",
                    "error_code": "datasource_not_found",
                    "message": "数据源不存在",
                },
            }
        }
    )

    assert result == {
        "error_code": "datasource_not_found",
        "message": "SQL 执行失败：数据源不存在",
        "retryable": False,
        "repair_hint": "请检查数据集绑定的数据源是否存在，或当前用户是否有访问权限。",
        "repair_plan": {
            "action": "check_datasource",
            "reason": "数据源不可用，不能自动重试",
            "retryable": False,
        },
    }


def test_sql_adapter_marks_repairable_table_error_with_retry_plan():
    adapter = SqlAdapter()

    result = adapter.handle_error(
        {
            "variables": {
                "sql": {"sql": "select * from missing_table"},
                "knowledge": {"tables": ["stall_traffic_1d"]},
                "sql_execution": {
                    "status": "failed",
                    "error_code": "sql_execute_error",
                    "message": "Table 'missing_table' doesn't exist",
                },
            }
        }
    )

    assert result == {
        "error_code": "sql_execute_error",
        "message": "SQL 执行失败：Table 'missing_table' doesn't exist",
        "retryable": True,
        "repair_hint": "表不存在，请基于已命中的语义表重新生成 SQL。",
        "repair_plan": {
            "action": "regenerate_sql",
            "reason": "SQL 引用了不存在的表",
            "retryable": True,
            "candidate_tables": ["stall_traffic_1d"],
        },
    }


def test_execute_split_preserves_sub_plan_role_for_share_analysis_e2e():
    """角色丢失就是 G3/G4 静默降级：R1 回归测试。"""
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=30,
            data_set_name="档口经营分析",
            id=30,
            name="档口经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[
            {
                "id": 10,
                "name": "档口流量模型",
                "biz_name": "stall_traffic",
                "datasource_id": 5,
                "tableQuery": "stall_traffic_1d",
                "dimensions": [
                    {"name": "档口", "bizName": "shop_name", "expr": "shop_name"}
                ],
                "measures": [
                    {"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}
                ],
            }
        ],
        metrics=[
            SchemaElement(
                data_set_id=30,
                data_set_name="档口经营分析",
                model=10,
                id=100,
                name="访问人数",
                biz_name="visit_uv",
                type="METRIC",
                default_agg="SUM",
            )
        ],
        dimensions=[
            SchemaElement(
                data_set_id=30,
                data_set_name="档口经营分析",
                model=10,
                id=200,
                name="档口",
                biz_name="shop_name",
                type="DIMENSION",
            )
        ],
    )
    # part 查询按档口分组返回 30+70，total 查询返回单行汇总 100。
    class RoleAwareFakeSqlExecuteTool:
        def run(self, payload):
            sql = payload.get("sql", "")
            if "shop_name" in sql:
                return ToolResult(
                    success=True,
                    payload={
                        "fields": ["shop_name", "visit_uv"],
                        "data": [
                            {"shop_name": "A", "visit_uv": 30},
                            {"shop_name": "B", "visit_uv": 70},
                        ],
                    },
                )
            return ToolResult(
                success=True,
                payload={"fields": ["visit_uv"], "data": [{"visit_uv": 100}]},
            )

    adapter = SqlAdapter(
        schema_provider=FakeDatasetSchemaProvider(schema),
        execute_tool=RoleAwareFakeSqlExecuteTool(),
    )

    # Step 1: generate_split with role-annotated sub_plans.
    plans = [
        {
            "role": "part",
            "model_id": 10,
            "metrics": [{"asset_type": "METRIC", "asset_id": 100}],
            "dimensions": [{"asset_type": "DIMENSION", "asset_id": 200}],
            "slots": {
                "metrics": [{"asset_type": "METRIC", "asset_id": 100, "display_name": "访问人数"}],
                "dimensions": [{"asset_type": "DIMENSION", "asset_id": 200, "display_name": "档口"}],
            },
        },
        {
            "role": "total",
            "model_id": 10,
            "metrics": [{"asset_type": "METRIC", "asset_id": 100}],
            "dimensions": [],
            "slots": {
                "metrics": [{"asset_type": "METRIC", "asset_id": 100, "display_name": "访问人数"}],
                "dimensions": [],
            },
        },
    ]
    generated = adapter.generate_split(
        {
            "request": {
                "question": "各档口销售额占比",
                "dataset_id": 30,
                "tenant_id": 10,
                "user_id": 20,
            },
            "variables": {
                "plan": {
                    "strategy": "multi_query",
                    "sub_plans": plans,
                }
            },
        }
    )
    assert len(generated["queries"]) == 2
    assert generated["queries"][0]["role"] == "part"
    assert generated["queries"][1]["role"] == "total"

    # Step 2: execute_split — role must survive through ExecutionQuery round-trip.
    exec_result = adapter.execute_split(
        {
            "request": {
                "question": "各档口销售额占比",
                "dataset_id": 30,
                "tenant_id": 10,
                "user_id": 20,
                "run_id": "run-share-role-e2e",
            },
            "variables": {"split_sql": generated},
        }
    )
    assert exec_result["status"] == "succeeded"
    assert exec_result["queries"][0]["role"] == "part"
    assert exec_result["queries"][1]["role"] == "total"

    # Step 3: answer projection sees the analysis block via role map.
    from apps.workflow.capabilities.adapters.answer import build_answer_projection

    projection = build_answer_projection(
        {
            "variables": {
                "plan": {
                    "strategy": "multi_query",
                    "sub_plans": plans,
                },
                "execution": exec_result,
                "question": {"rewritten": "各档口销售额占比"},
                "knowledge": {},
            }
        }
    )
    assert projection["execution"].get("analysis", {}).get("kind") == "share"
    analysis = projection["execution"]["analysis"]
    assert analysis["total"] == 100.0
    assert len(analysis["rows"]) == 2
