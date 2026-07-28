"""核心工具的守护行为测试（不依赖真实 DB/LLM）。"""

from apps.chatbi.models import (
    ChatBIResultArtifactRef,
    PhysicalSchemaField,
    PhysicalSchemaResult,
    PhysicalSchemaTable,
    SemanticQueryCompileResult,
)
from apps.chatbi.orchestration.agent.tool_execution import (
    _apply_chatbi_tool_result,
)
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.orchestration.agent.tools.core import (
    CompileSemanticSqlArgs,
    CompileSemanticSqlTool,
    FinishArgs,
    FinishTool,
    SearchSemanticAssetsArgs,
    SearchSemanticAssetsTool,
)
from apps.datasource import (
    DatasourceQueryData,
    DatasourceQueryErrorCategory,
    DatasourceQueryPolicy,
    DatasourceQueryResult,
    DatasourceQueryRetryAdvice,
)
from apps.tool import RetryAdvice, ToolStatus
from apps.tool import ToolResult as AgentToolResult
from apps.tool.tools.datasource import (
    ExecuteSqlArgs,
    ExecuteSqlResult,
    ExecuteSqlTool,
    GetDatasetSchemaArgs,
    GetDatasetSchemaTool,
    ValidateSqlArgs,
    ValidateSqlTool,
)
from apps.tool.tools.knowledge import (
    GetSqlExamplesArgs,
    GetSqlExamplesTool,
)


def _succeeded(result: AgentToolResult) -> bool:
    return result.status == ToolStatus.SUCCEEDED


def _data(result: AgentToolResult) -> dict:
    assert result.data is not None
    return result.data.model_dump(mode="json")


def _ctx(
    result_artifact_service=None,
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
        execution_id="agent:10",
        chat_id=20,
        record_id=30,
        result_artifact_service=(
            result_artifact_service or RecordingResultArtifactService()
        ),
        state=values,
    )


class RecordingQueryService:
    def __init__(self) -> None:
        self.validate_calls = []
        self.execute_calls = []

    def validate(self, request) -> DatasourceQueryResult:
        self.validate_calls.append(request)
        return DatasourceQueryResult.succeeded(
            DatasourceQueryData(
                sql=f"{request.sql} limit 100",
                tables=["orders"],
                effective_tables=["orders"],
            )
        )

    def execute(self, request) -> DatasourceQueryResult:
        self.execute_calls.append(request)
        return DatasourceQueryResult.succeeded(
            DatasourceQueryData(
                sql="select amount from orders limit 100",
                tables=["orders"],
                effective_tables=["orders"],
                fields=["amount"],
                sample_rows=[{"amount": 10}],
                row_count=2,
                stats_summary={"amount": {"sum": 30.0}},
                full_data=[{"amount": 10}, {"amount": 20}],
            )
        )

    def resolve_policy(self, subject, datasource_id):
        return DatasourceQueryPolicy(authorized_tables=["orders", "dws_sales"])


class RecordingResultArtifactService:
    def __init__(self) -> None:
        self.calls = []

    def save(self, data):
        self.calls.append(data)
        return ChatBIResultArtifactRef(
            artifact_id="result-1",
            kind=data.kind,
            content_type="application/json",
            size=10,
            digest="sha256:test",
            metadata={
                "execution_id": data.execution_id,
                "execution_type": data.execution_type.value,
                "chat_id": data.chat_id,
                "record_id": data.record_id,
            },
        )


class RecordingSemanticCompilationService:
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

    def filter_authorized_tables(self, package, authorized_tables):
        return package


class StaticPhysicalSchemaService:
    def get(self, datasource_id, *, subject, table_keyword=""):
        assert datasource_id == 5
        assert subject.model_dump() == {"user_id": 1, "workspace_id": 1}
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


class DeniedPhysicalSchemaService:
    def get(self, datasource_id, *, subject, table_keyword=""):
        raise PermissionError("physical_schema_access_denied")


class StaticSqlExampleQueryService:
    def __init__(self, items=None) -> None:
        self.items = items or []
        self.calls = []

    def search(
        self,
        question,
        workspace_id,
        *,
        datasource_id=None,
        assistant_id=None,
    ):
        self.calls.append((question, workspace_id, datasource_id, assistant_id))
        return self.items


def test_finish_rejected_without_execution():
    output = FinishTool().execute(_ctx(), FinishArgs(answer_markdown="答案"))
    assert not _succeeded(output)
    assert output.error_code == "execution_required_before_finish"


def test_validate_sql_uses_chatbi_query_service():
    service = RecordingQueryService()
    ctx = _ctx(allowed_tables=["orders"])

    output = ValidateSqlTool(service).execute(
        ctx,
        ValidateSqlArgs(sql="select amount from orders"),
    )

    assert _succeeded(output)
    assert service.validate_calls[0].sql == "select amount from orders"
    assert service.validate_calls[0].selected_tables == ["orders"]
    assert service.validate_calls[0].subject.model_dump() == {
        "user_id": 1,
        "workspace_id": 1,
    }


def test_validate_sql_maps_datasource_authorization_rejection():
    class RejectedQueryService(RecordingQueryService):
        def validate(self, request):
            return DatasourceQueryResult.rejected(
                "表无访问权限",
                error_code="table_out_of_scope",
                error_category=DatasourceQueryErrorCategory.AUTHORIZATION,
            )

    output = ValidateSqlTool(RejectedQueryService()).execute(
        _ctx(allowed_tables=["orders"]),
        ValidateSqlArgs(sql="select * from secret_orders"),
    )

    assert output.status == ToolStatus.REJECTED
    assert output.error_code == "table_out_of_scope"


def test_execute_sql_uses_chatbi_query_service_with_identity_scope():
    service = RecordingQueryService()
    artifact_service = RecordingResultArtifactService()
    ctx = _ctx(
        result_artifact_service=artifact_service,
        allowed_tables=["orders"],
        compiled_sql="select amount from orders",
    )

    output = ExecuteSqlTool(service).execute(
        ctx,
        ExecuteSqlArgs(sql="select amount from orders"),
    )

    assert _succeeded(output)
    request = service.execute_calls[0]
    assert request.sql == "select amount from orders"
    assert request.datasource_id == 5
    assert request.subject.model_dump() == {"user_id": 1, "workspace_id": 1}
    assert request.selected_tables == ["orders"]
    assert output.metadata["full_data"] == [
        {"amount": 10},
        {"amount": 20},
    ]
    assert artifact_service.calls == []


def test_finish_appends_non_standard_note_for_manual_sql():
    ctx = _ctx(last_execution={"sql": "select 1", "fields": ["a"], "row_count": 1, "sql_source": "manual"})
    output = FinishTool().execute(ctx, FinishArgs(answer_markdown="答案"))
    assert _succeeded(output)
    assert "非标准指标口径" in _data(output)["answer"]
    assert _data(output)["non_standard"] is True


def test_finish_no_note_for_compiled_sql_and_builds_chart():
    ctx = _ctx(last_execution={"sql": "select 1", "fields": ["city", "gmv"], "row_count": 3, "sql_source": "compiled"})
    output = FinishTool().execute(ctx, FinishArgs(answer_markdown="答案", chart_type="bar", x_field="city", y_fields=["gmv"]))
    assert _succeeded(output)
    assert "非标准" not in _data(output)["answer"]
    assert _data(output)["chart"] == {"type": "bar", "x": "city", "y": ["gmv"]}
    assert _data(output)["non_standard"] is False


def test_execute_sql_does_not_write_chatbi_state_or_artifact():
    service = RecordingQueryService()
    artifact_service = RecordingResultArtifactService()
    ctx = _ctx(
        result_artifact_service=artifact_service,
        allowed_tables=["orders"],
    )

    output = ExecuteSqlTool(service).execute(
        ctx,
        ExecuteSqlArgs(sql="select amount from orders"),
    )

    assert _succeeded(output)
    assert "last_execution" not in ctx.state
    assert "full_data" not in ctx.state
    assert artifact_service.calls == []


def test_chatbi_execution_boundary_saves_public_sql_result_artifact():
    artifact_service = RecordingResultArtifactService()
    ctx = _ctx(
        result_artifact_service=artifact_service,
        compiled_sql="select amount from orders",
    )
    result = AgentToolResult.succeeded(
        "summary",
        ExecuteSqlResult(
            sql="select amount from orders",
            fields=["amount"],
            sample_rows=[{"amount": 10}],
            row_count=2,
            stats_summary={"amount": {"sum": 30}},
        ),
        metadata={"full_data": [{"amount": 10}, {"amount": 20}]},
    )

    projected = _apply_chatbi_tool_result(ctx, "execute_sql", result)

    assert _succeeded(projected)
    assert artifact_service.calls[0].payload["rows"] == [
        {"amount": 10},
        {"amount": 20},
    ]
    assert ctx.state["last_execution"]["sql_source"] == "compiled"
    assert ctx.state["full_data"] == [{"amount": 10}, {"amount": 20}]


def test_execute_sql_maps_transient_failure_to_same_input_retry():
    class TransientQueryService(RecordingQueryService):
        def execute(self, request):
            return DatasourceQueryResult.failed(
                "连接超时",
                error_code="connection_timeout",
                error_category=DatasourceQueryErrorCategory.TRANSIENT,
                retry_advice=DatasourceQueryRetryAdvice.SAME_INPUT,
            )

    output = ExecuteSqlTool(TransientQueryService()).execute(
        _ctx(allowed_tables=["orders"]),
        ExecuteSqlArgs(sql="select amount from orders"),
    )

    assert output.status == ToolStatus.FAILED
    assert output.retry_advice == RetryAdvice.SAME_INPUT


def test_compile_requires_semantic_package_first():
    ctx = _ctx(dataset_id=3)
    output = CompileSemanticSqlTool(RecordingSemanticCompilationService()).execute(
        ctx,
        CompileSemanticSqlArgs(metric_asset_ids=[1]),
    )
    assert not _succeeded(output)
    assert output.error_code == "semantic_package_required"


def test_compile_does_not_repeat_question_understanding_gate():
    service = RecordingSemanticCompilationService()
    ctx = _ctx(
        dataset_id=3,
        semantic_asset_ids=[10],
        question_understanding={
            "rewritten_question": "看一下最近7天的数据",
            "intent": {"intent_type": "metric_query", "metric_mentions": []},
            "validation": {"status": "clarification_required", "clarification_slots": ["metric"]},
        },
    )

    output = CompileSemanticSqlTool(service).execute(
        ctx,
        CompileSemanticSqlArgs(metric_asset_ids=[10]),
    )

    assert _succeeded(output)
    assert len(service.calls) == 1


def test_compile_rejects_asset_outside_package():
    ctx = _ctx(dataset_id=3, semantic_asset_ids=[10, 11])
    output = CompileSemanticSqlTool(RecordingSemanticCompilationService()).execute(
        ctx,
        CompileSemanticSqlArgs(metric_asset_ids=[10], dimension_asset_ids=[99]),
    )
    assert not _succeeded(output)
    assert output.error_code == "asset_not_in_package"
    assert "99" in output.model_content


def test_compile_passes_known_assets_to_capability():
    service = RecordingSemanticCompilationService()
    ctx = _ctx(
        dataset_id=3,
        semantic_asset_ids=[10, 11],
    )
    output = CompileSemanticSqlTool(service).execute(
        ctx, CompileSemanticSqlArgs(metric_asset_ids=[10], dimension_asset_ids=[11])
    )
    assert _succeeded(output)
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
    service = RecordingSemanticCompilationService()
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
                    "normalized": normalized_time,
                },
            },
            "validation": {"status": "valid"},
        },
    )

    output = CompileSemanticSqlTool(service).execute(
        ctx,
        CompileSemanticSqlArgs(
            metric_asset_ids=[10],
            filters=[{"asset_id": 11, "operator": "=", "value": "today"}],
        ),
    )

    assert _succeeded(output)
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

    output = CompileSemanticSqlTool(RecordingSemanticCompilationService()).execute(
        ctx,
        CompileSemanticSqlArgs(
            metric_asset_ids=[10],
            filters=[{"asset_id": 11, "operator": "=", "value": "2026-06-30"}],
        ),
    )

    assert not _succeeded(output)
    assert output.error_code == "time_filter_mismatch"
    assert "禁止省略时间或替换成数据最大日期" in output.model_content


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
        dataset_id=3,
        question_understanding={
            "rewritten_question": "按城市看 gmv",
            "intent": intent,
            "validation": {"status": "valid"},
        },
    )
    output = SearchSemanticAssetsTool(
        service,
        RecordingQueryService(),
    ).execute(ctx, SearchSemanticAssetsArgs())
    assert _succeeded(output)
    request = service.calls[0][0]
    assert request.rewritten_question == "按城市看 gmv"
    assert request.intent is intent
    assert ctx.state["semantic_asset_ids"] == [7, 8]
    assert ctx.state["allowed_tables"] == ["dws_sales"]
    assert ctx.state["semantic_package"] == package


def test_physical_schema_tool_uses_chatbi_service():
    ctx = _ctx()

    output = GetDatasetSchemaTool(StaticPhysicalSchemaService()).execute(
        ctx,
        GetDatasetSchemaArgs(table_keyword="订单"),
    )

    assert _succeeded(output)
    assert _data(output)["tables"][0]["fields"][0] == {
        "name": "amount",
        "type": "numeric",
        "comment": "订单金额",
    }
    assert "allowed_tables" not in ctx.state


def test_physical_schema_tool_maps_access_denial():
    output = GetDatasetSchemaTool(DeniedPhysicalSchemaService()).execute(
        _ctx(),
        GetDatasetSchemaArgs(),
    )

    assert output.status == ToolStatus.REJECTED
    assert output.error_code == "physical_schema_access_denied"


def test_sql_examples_tool_uses_injected_service_without_session_build():
    service = StaticSqlExampleQueryService(
        [{"question": "销售额", "suggestion_answer": "select 1"}]
    )
    output = GetSqlExamplesTool(service).execute(
        _ctx(),
        GetSqlExamplesArgs(question="销售额"),
    )

    assert _succeeded(output)
    assert _data(output)["count"] == 1
    assert service.calls == [("销售额", 1, 5, None)]


def test_search_rejects_missing_confirmed_understanding():
    output = SearchSemanticAssetsTool(
        RecordingSemanticRetrievalService({}),
        RecordingQueryService(),
    ).execute(
        _ctx(dataset_id=3, question_understanding=None),
        SearchSemanticAssetsArgs(),
    )

    assert not _succeeded(output)
    assert output.error_code == "question_understanding_required"
