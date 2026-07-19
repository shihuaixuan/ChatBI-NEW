"""核心工具的守护行为测试（不依赖真实 DB/LLM）。"""

from apps.agent.tools.base import AgentToolContext
from apps.agent.tools.core import (
    CompileSemanticSqlArgs,
    CompileSemanticSqlTool,
    ExecuteSqlArgs,
    ExecuteSqlTool,
    FinishArgs,
    FinishTool,
    GetDatasetSchemaArgs,
    GetDatasetSchemaTool,
    SearchSemanticAssetsArgs,
    SearchSemanticAssetsTool,
    ValidateSqlArgs,
    ValidateSqlTool,
)
from apps.capabilities.schemas import ToolResult
from apps.chatbi.models import (
    PhysicalSchemaField,
    PhysicalSchemaResult,
    PhysicalSchemaTable,
    SemanticQueryCompileResult,
)


def _ctx(
    query_service=None,
    semantic_query_service=None,
    semantic_retrieval_service=None,
    physical_schema_service=None,
    **state,
):
    values = {
        "question_understanding": {
            "rewritten_question": "本月销售额",
            "intent": {"intent_type": "metric_query", "metric_mentions": ["销售额"]},
            "validation": {"status": "valid"},
        }
    }
    values.update(state)
    return AgentToolContext(
        session=None,
        oid=1,
        user_id=1,
        datasource_id=5,
        query_service=query_service,
        semantic_query_service=semantic_query_service,
        semantic_retrieval_service=semantic_retrieval_service,
        physical_schema_service=physical_schema_service,
        state=values,
    )


class RecordingQueryService:
    def __init__(self) -> None:
        self.validate_calls: list[tuple[str, list[str]]] = []
        self.execute_calls: list[dict] = []

    def validate_sql(self, sql: str, *, allowed_tables=None) -> ToolResult:
        self.validate_calls.append((sql, allowed_tables or []))
        return ToolResult(success=True, payload={"sql": f"{sql} limit 100"})

    def execute_sql(self, **payload) -> ToolResult:
        self.execute_calls.append(payload)
        return ToolResult(
            success=True,
            payload={
                "sql": "select amount from orders limit 100",
                "fields": ["amount"],
                "sample_rows": [{"amount": 10}],
                "row_count": 2,
                "stats_summary": {"amount": {"sum": 30.0}},
                "full_data": [{"amount": 10}, {"amount": 20}],
                "artifact_ref": {"artifact_id": "result-1"},
            },
        )


class RecordingSemanticQueryService:
    def __init__(self) -> None:
        self.calls = []

    def compile(self, data):
        self.calls.append(data)
        return SemanticQueryCompileResult(
            dataset_id=data.dataset_id,
            sql="select 1",
            tables=["t"],
            metrics=["gmv"],
            dimensions=["city"],
            datasource_id=5,
            used_assets=[],
        )


class RecordingSemanticRetrievalService:
    def __init__(self, package) -> None:
        self.package = package
        self.calls = []

    def retrieve_for_agent(self, data, *, max_candidates_per_group=5):
        self.calls.append((data, max_candidates_per_group))
        return self.package


class StaticPhysicalSchemaService:
    def get(self, datasource_id, *, table_keyword=""):
        assert datasource_id == 5
        assert table_keyword == "订单"
        return PhysicalSchemaResult(
            tables=[
                PhysicalSchemaTable(
                    name="orders",
                    comment="订单表",
                    fields=[
                        PhysicalSchemaField(
                            name="amount",
                            data_type="numeric",
                            comment="订单金额",
                        )
                    ],
                )
            ]
        )


def test_finish_rejected_without_execution():
    output = FinishTool().execute(_ctx(), FinishArgs(answer_markdown="答案"))
    assert not output.success
    assert output.error_code == "execution_required_before_finish"


def test_validate_sql_uses_chatbi_query_service():
    service = RecordingQueryService()
    ctx = _ctx(query_service=service, allowed_tables=["orders"])

    output = ValidateSqlTool().execute(
        ctx,
        ValidateSqlArgs(sql="select amount from orders"),
    )

    assert output.success
    assert service.validate_calls == [
        ("select amount from orders", ["orders"])
    ]


def test_execute_sql_uses_chatbi_query_service_with_identity_scope():
    service = RecordingQueryService()
    ctx = _ctx(
        query_service=service,
        allowed_tables=["orders"],
        compiled_sql="select amount from orders",
    )

    output = ExecuteSqlTool().execute(
        ctx,
        ExecuteSqlArgs(sql="select amount from orders"),
    )

    assert output.success
    assert service.execute_calls == [
        {
            "sql": "select amount from orders",
            "datasource_id": 5,
            "workspace_id": 1,
            "user_id": 1,
            "allowed_tables": ["orders"],
        }
    ]
    assert ctx.state["full_data"] == [{"amount": 10}, {"amount": 20}]
    assert output.payload["sql_source"] == "compiled"


def test_finish_appends_non_standard_note_for_manual_sql():
    ctx = _ctx(last_execution={"sql": "select 1", "fields": ["a"], "row_count": 1, "sql_source": "manual"})
    output = FinishTool().execute(ctx, FinishArgs(answer_markdown="答案"))
    assert output.success
    assert "非标准指标口径" in output.payload["answer"]
    assert output.payload["non_standard"] is True


def test_finish_no_note_for_compiled_sql_and_builds_chart():
    ctx = _ctx(last_execution={"sql": "select 1", "fields": ["city", "gmv"], "row_count": 3, "sql_source": "compiled"})
    output = FinishTool().execute(ctx, FinishArgs(answer_markdown="答案", chart_type="bar", x_field="city", y_fields=["gmv"]))
    assert output.success
    assert "非标准" not in output.payload["answer"]
    assert output.payload["chart"] == {"type": "bar", "x": "city", "y": ["gmv"]}
    assert output.payload["non_standard"] is False


def test_execute_sql_preserves_artifact_reference_for_record_projection():
    ctx = _ctx(query_service=RecordingQueryService())

    output = ExecuteSqlTool().execute(ctx, ExecuteSqlArgs(sql="select amount from orders"))

    assert output.success
    assert ctx.state["last_execution"]["artifact_ref"] == {"artifact_id": "result-1"}


def test_compile_requires_semantic_package_first():
    ctx = _ctx(dataset_id=3)
    output = CompileSemanticSqlTool().execute(ctx, CompileSemanticSqlArgs(metric_asset_ids=[1]))
    assert not output.success
    assert output.error_code == "semantic_package_required"


def test_compile_rejects_intent_that_still_requires_clarification():
    ctx = _ctx(
        dataset_id=3,
        semantic_asset_ids=[10],
        question_understanding={
            "rewritten_question": "看一下最近7天的数据",
            "intent": {"intent_type": "metric_query", "metric_mentions": []},
            "validation": {"status": "clarification_required", "clarification_slots": ["metric"]},
        },
    )

    output = CompileSemanticSqlTool().execute(ctx, CompileSemanticSqlArgs(metric_asset_ids=[10]))

    assert not output.success
    assert output.error_code == "question_clarification_required"


def test_compile_rejects_asset_outside_package():
    ctx = _ctx(dataset_id=3, semantic_asset_ids=[10, 11])
    output = CompileSemanticSqlTool().execute(ctx, CompileSemanticSqlArgs(metric_asset_ids=[10], dimension_asset_ids=[99]))
    assert not output.success
    assert output.error_code == "asset_not_in_package"
    assert "99" in output.summary


def test_compile_passes_known_assets_to_capability():
    service = RecordingSemanticQueryService()
    ctx = _ctx(
        semantic_query_service=service,
        dataset_id=3,
        semantic_asset_ids=[10, 11],
    )
    output = CompileSemanticSqlTool().execute(
        ctx, CompileSemanticSqlArgs(metric_asset_ids=[10], dimension_asset_ids=[11])
    )
    assert output.success
    slots = service.calls[0].slots
    assert slots["metrics"] == [{"asset_id": 10, "asset_type": "METRIC"}]
    assert slots["dimensions"] == [{"asset_id": 11, "asset_type": "DIMENSION"}]
    assert ctx.state["compiled_sql"] == "select 1"
    assert "t" in ctx.state["allowed_tables"]


def test_compile_normalizes_today_literal_from_confirmed_time_range():
    normalized_time = {
        "kind": "single_date",
        "anchor": "today",
        "offset_days": 0,
        "timezone": "Asia/Shanghai",
    }
    service = RecordingSemanticQueryService()
    ctx = _ctx(
        semantic_query_service=service,
        dataset_id=3,
        semantic_asset_ids=[10, 11],
        question_understanding={
            "rewritten_question": "今天销售额",
            "intent": {
                "intent_type": "metric_query",
                "metric_mentions": ["销售额"],
                "time_range": {
                    "raw": "今天",
                    "value_status": "provided",
                    "normalized": normalized_time,
                },
            },
            "validation": {"status": "valid"},
        },
    )

    output = CompileSemanticSqlTool().execute(
        ctx,
        CompileSemanticSqlArgs(
            metric_asset_ids=[10],
            filters=[{"asset_id": 11, "operator": "=", "value": "today"}],
        ),
    )

    assert output.success
    assert service.calls[0].slots["filters"] == [
        {
            "asset_id": 11,
            "asset_type": "DIMENSION",
            "operator": "=",
            "value": normalized_time,
        }
    ]


def test_compile_rejects_replacing_today_with_latest_data_date():
    ctx = _ctx(
        dataset_id=3,
        semantic_asset_ids=[10, 11],
        question_understanding={
            "rewritten_question": "今天销售额",
            "intent": {
                "intent_type": "metric_query",
                "metric_mentions": ["销售额"],
                "time_range": {
                    "raw": "今天",
                    "value_status": "provided",
                    "normalized": {
                        "kind": "single_date",
                        "anchor": "today",
                        "offset_days": 0,
                        "timezone": "Asia/Shanghai",
                    },
                },
            },
            "validation": {"status": "valid"},
        },
    )

    output = CompileSemanticSqlTool().execute(
        ctx,
        CompileSemanticSqlArgs(
            metric_asset_ids=[10],
            filters=[{"asset_id": 11, "operator": "=", "value": "2026-06-30"}],
        ),
    )

    assert not output.success
    assert output.error_code == "time_filter_mismatch"
    assert "禁止省略时间或替换成数据最大日期" in output.summary


def test_search_collects_asset_ids_and_tables_into_state():
    intent = {
        "intent_type": "metric_query",
        "metric_mentions": ["gmv"],
        "dimension_mentions": ["城市"],
        "dimension_slots": [{"name": "城市", "role": "group_by"}],
        "time_mentions": [],
    }
    package = {
        "hit": True,
        "status": "hit",
        "tables": ["dws_sales"],
        "candidate_groups": {"metrics": [{"asset_id": 7, "biz_name": "gmv"}]},
        "selected_assets": {"dimensions": [{"asset_id": 8, "biz_name": "city"}]},
    }
    service = RecordingSemanticRetrievalService(package)
    ctx = _ctx(
        semantic_retrieval_service=service,
        dataset_id=3,
        question_understanding={
            "rewritten_question": "按城市看 gmv",
            "intent": intent,
            "validation": {"status": "valid"},
        },
    )
    output = SearchSemanticAssetsTool().execute(ctx, SearchSemanticAssetsArgs())
    assert output.success
    request = service.calls[0][0]
    assert request.rewritten_question == "按城市看 gmv"
    assert request.intent is intent
    assert ctx.state["semantic_asset_ids"] == [7, 8]
    assert ctx.state["allowed_tables"] == ["dws_sales"]
    assert ctx.state["semantic_package"] is package


def test_physical_schema_tool_uses_chatbi_service():
    ctx = _ctx(physical_schema_service=StaticPhysicalSchemaService())

    output = GetDatasetSchemaTool().execute(
        ctx,
        GetDatasetSchemaArgs(table_keyword="订单"),
    )

    assert output.success
    assert output.payload["tables"][0]["fields"][0] == {
        "name": "amount",
        "type": "numeric",
        "comment": "订单金额",
    }
    assert ctx.state["allowed_tables"] == ["orders"]


def test_search_rejects_missing_confirmed_understanding():
    output = SearchSemanticAssetsTool().execute(
        _ctx(dataset_id=3, question_understanding=None),
        SearchSemanticAssetsArgs(),
    )

    assert not output.success
    assert output.error_code == "question_understanding_required"
