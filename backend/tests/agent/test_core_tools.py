"""核心工具的守护行为测试（不依赖真实 DB/LLM）。"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from apps.chatbi.models import (
    ChatBIResultArtifactRef,
    PhysicalSchemaField,
    PhysicalSchemaResult,
    PhysicalSchemaTable,
)
from apps.chatbi.orchestration.agent.tool_results import (
    ChatBIToolResultProcessor,
)
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.orchestration.agent.tools.core import (
    FinishArgs,
    FinishTool,
)
from apps.chatbi.orchestration.agent.tools.interaction import ClarifyArgs, ClarifyTool
from apps.datasource import (
    DatasourceQueryData,
    DatasourceQueryErrorCategory,
    DatasourceQueryPolicy,
    DatasourceQueryResult,
    DatasourceQueryRetryAdvice,
)
from apps.retrieval import (
    ExecutableAssetReference,
    RetrievalDecisionStatus,
)
from apps.retrieval.models.dto import (
    AssetReference,
    RetrievalAmbiguity,
    RetrievalBindings,
    RetrievalBundle,
    RetrievalDecision,
    RetrievalDiagnostics,
    RetrievalPurpose,
    RetrievalResourceType,
    RetrievalSlotDecision,
)
from apps.semantic import SemanticUsedAsset
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
from apps.tool.tools.semantic import (
    CompileSemanticSqlArgs,
    CompileSemanticSqlTool,
    SearchSemanticAssetsArgs,
    SearchSemanticAssetsTool,
)
from apps.tool.tools.semantic_contracts import (
    SemanticAssetScope,
    project_semantic_compile_plan,
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
    semantic_asset_ids = values.get("semantic_asset_ids") or []
    if semantic_asset_ids and "semantic_scope" not in values:
        normalized_time = (
            (values.get("question_understanding") or {})
            .get("intent", {})
            .get("time_range", {})
            .get("normalized")
        )
        values["semantic_scope"] = SemanticAssetScope(
            workspace_id=1,
            user_id=1,
            datasource_id=5,
            dataset_id=int(values.get("dataset_id") or 3),
            retrieval_id="test-retrieval",
            decision_status=RetrievalDecisionStatus.RESOLVED,
            allowed_assets=tuple(
                ExecutableAssetReference(asset_type=asset_type, asset_id=asset_id)
                for asset_id in semantic_asset_ids
                for asset_type in (
                    RetrievalResourceType.METRIC,
                    RetrievalResourceType.DIMENSION,
                )
            ),
            normalized_time_range=normalized_time,
        ).model_dump(mode="json")
    return AgentToolContext(
        session=None,
        oid=1,
        user_id=1,
        datasource_id=5,
        dataset_id=(
            int(values["dataset_id"])
            if isinstance(values.get("dataset_id"), int)
            else None
        ),
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
        return DatasourceQueryPolicy(authorized_tables=["orders", "dws_sales", "t"])


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
        metric_ids = [item["asset_id"] for item in data.slots.get("metrics", [])]
        dimension_ids = [item["asset_id"] for item in data.slots.get("dimensions", [])]
        if (
            isinstance(data.time_bucket, dict)
            and isinstance(data.time_bucket.get("dimension_id"), int)
            and data.time_bucket["dimension_id"] not in dimension_ids
        ):
            dimension_ids.append(data.time_bucket["dimension_id"])
        sql_parts = ["select 1"]
        if dimension_ids and (data.order_by or data.time_bucket):
            sql_parts.append("group by dimension_value")
        if data.order_by:
            sql_parts.append("order by metric_value desc")
        if data.order_by and data.limit is not None:
            sql_parts.append(f"limit {data.limit}")
        return SimpleNamespace(
            dataset_id=data.dataset_id,
            sql=" ".join(sql_parts),
            tables=["t"],
            metrics=["gmv"],
            dimensions=["city"],
            datasource_id=5,
            used_assets=[
                *[
                    SemanticUsedAsset(
                        asset_type="METRIC",
                        asset_id=asset_id,
                        biz_name=f"metric_{asset_id}",
                    )
                    for asset_id in metric_ids
                ],
                *[
                    SemanticUsedAsset(
                        asset_type="DIMENSION",
                        asset_id=asset_id,
                        biz_name=f"dimension_{asset_id}",
                    )
                    for asset_id in dimension_ids
                ],
            ],
        )


class IncompleteSemanticCompilationService:
    def __init__(self) -> None:
        self.calls = []

    def compile(self, data):
        self.calls.append(data)
        return SimpleNamespace(
            dataset_id=data.dataset_id,
            sql="select metric_value, dimension_value from t",
            tables=["t"],
            metrics=["gmv"],
            dimensions=["city"],
            datasource_id=5,
            used_assets=[
                SemanticUsedAsset("METRIC", 100, "gmv"),
                SemanticUsedAsset("DIMENSION", 200, "city"),
            ],
        )


class RecordingSemanticRetrievalService:
    def __init__(self, package) -> None:
        self.package = package
        self.calls = []

    def retrieve(self, request, *, timeout_ms=None):
        self.calls.append((request, timeout_ms))
        allowed_assets = [
            ExecutableAssetReference.model_validate(item)
            for item in self.package.get("allowed_asset_ids") or []
        ]
        decision_status = (self.package.get("decision") or {}).get(
            "status"
        ) or "resolved"
        return SimpleNamespace(
            payload=self.package,
            bundle=SimpleNamespace(
                request_id=request.request_id,
                decision=SimpleNamespace(
                    status=RetrievalDecisionStatus(decision_status),
                    allowed_asset_ids=allowed_assets,
                ),
            ),
        )


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
    ctx = _ctx(
        last_execution={
            "sql": "select 1",
            "fields": ["a"],
            "row_count": 1,
            "sql_source": "manual",
        }
    )
    output = FinishTool().execute(ctx, FinishArgs(answer_markdown="答案"))
    assert _succeeded(output)
    assert "非标准指标口径" in _data(output)["answer"]
    assert _data(output)["non_standard"] is True


def test_finish_no_note_for_compiled_sql_and_builds_chart():
    ctx = _ctx(
        last_execution={
            "sql": "select 1",
            "fields": ["city", "gmv"],
            "row_count": 3,
            "sql_source": "compiled",
        }
    )
    output = FinishTool().execute(
        ctx,
        FinishArgs(
            answer_markdown="答案", chart_type="bar", x_field="city", y_fields=["gmv"]
        ),
    )
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


def test_chatbi_result_processor_saves_public_sql_result_artifact():
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

    projection = ChatBIToolResultProcessor().process(
        ctx,
        "execute_sql",
        result,
    )

    assert _succeeded(projection.result)
    assert artifact_service.calls[0].payload["rows"] == [
        {"amount": 10},
        {"amount": 20},
    ]
    assert projection.state_patch["last_execution"]["sql_source"] == "compiled"
    assert projection.state_patch["full_data"] == [
        {"amount": 10},
        {"amount": 20},
    ]
    assert "last_execution" not in ctx.state


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
    output = CompileSemanticSqlTool(
        RecordingSemanticCompilationService(), RecordingQueryService()
    ).execute(
        ctx,
        CompileSemanticSqlArgs(metric_asset_ids=[1]),
    )
    assert not _succeeded(output)
    assert output.error_code == "semantic_package_required"


def test_compile_args_reject_unknown_asset_fields():
    with pytest.raises(ValidationError, match="asset_ids"):
        CompileSemanticSqlArgs.model_validate(
            {
                "asset_ids": [10, 11],
                "group_dimension_ids": [11],
                "time_range": {"anchor": "today"},
            }
        )


def test_compile_reports_ambiguous_decision_details():
    ctx = _ctx(dataset_id=3)
    ctx.state["semantic_scope"] = SemanticAssetScope(
        workspace_id=1,
        user_id=1,
        datasource_id=5,
        dataset_id=3,
        retrieval_id="ambiguous-retrieval",
        decision_status=RetrievalDecisionStatus.AMBIGUOUS,
    ).model_dump(mode="json")

    output = CompileSemanticSqlTool(
        RecordingSemanticCompilationService(), RecordingQueryService()
    ).execute(ctx, CompileSemanticSqlArgs(metric_asset_ids=[10]))

    assert output.status == ToolStatus.REJECTED
    assert output.error_code == "semantic_decision_not_executable"
    assert output.details["decision_status"] == "ambiguous"
    assert output.details["retry_action"] == "clarify_semantic_binding"


def test_compile_uses_trusted_plan_when_optional_retrieval_channel_is_degraded():
    ctx = _ctx(dataset_id=3)
    ctx.state["semantic_scope"] = SemanticAssetScope(
        workspace_id=1,
        user_id=1,
        datasource_id=5,
        dataset_id=3,
        retrieval_id="degraded-retrieval",
        decision_status=RetrievalDecisionStatus.DEGRADED,
        allowed_assets=(
            ExecutableAssetReference(
                asset_type=RetrievalResourceType.METRIC,
                asset_id=10,
            ),
        ),
        authorized_tables=("t",),
        compile_plan=project_semantic_compile_plan(
            {
                "metrics": [{"asset_type": "METRIC", "asset_id": 10}],
                "group_dimensions": [],
                "dimension_filters": [],
                "time_dimensions": [],
                "time_filters": [],
            },
            {"intent_type": "metric_query", "query_shape": {}},
        ),
    ).model_dump(mode="json")
    service = RecordingSemanticCompilationService()

    output = CompileSemanticSqlTool(service, RecordingQueryService()).execute(
        ctx,
        CompileSemanticSqlArgs(),
    )

    assert _succeeded(output)
    assert service.calls[0].slots["metrics"] == [
        {"asset_type": "METRIC", "asset_id": 10}
    ]


def _ambiguous_metric_clarification_context():
    ctx = _ctx(dataset_id=3)
    asset = AssetReference(
        asset_type=RetrievalResourceType.METRIC,
        asset_id=274,
        model_id=246,
    )
    bundle = RetrievalBundle(
        request_id="ambiguous-retrieval",
        bindings=RetrievalBindings(),
        decision=RetrievalDecision(
            status=RetrievalDecisionStatus.AMBIGUOUS,
            slot_decisions=[
                RetrievalSlotDecision(
                    subquery_id="metric:1",
                    purpose=RetrievalPurpose.METRIC,
                    status=RetrievalDecisionStatus.AMBIGUOUS,
                    candidate_assets=[asset],
                )
            ],
            ambiguities=[
                RetrievalAmbiguity(
                    subquery_id="metric:1",
                    reason_code="MULTIPLE_IDENTITY_MATCHES",
                    candidate_assets=[asset],
                )
            ],
        ),
        diagnostics=RetrievalDiagnostics(
            strategy_version="semantic-binding",
            index_generation="generation-1",
        ),
    )
    ctx.state["semantic_scope"] = SemanticAssetScope(
        workspace_id=1,
        user_id=1,
        datasource_id=5,
        dataset_id=3,
        retrieval_id="ambiguous-retrieval",
        decision_status=RetrievalDecisionStatus.AMBIGUOUS,
    ).model_dump(mode="json")
    ctx.state["semantic_bundle"] = bundle.model_dump(mode="json")
    ctx.state["semantic_payload"] = {
        "candidate_groups": {
            "metrics": [
                {
                    "asset_type": "METRIC",
                    "asset_id": 274,
                    "model_id": 246,
                    "display_name": "总下单客户数",
                    "biz_name": "order_customer_cnt_total",
                }
            ]
        }
    }
    ctx.state["semantic_retrieval_filters"] = {
        "subqueries": [{"subquery_id": "metric:1", "required": True}]
    }
    return ctx


def test_semantic_clarification_infers_binding_from_exact_candidate_name():
    ctx = _ambiguous_metric_clarification_context()

    output = ClarifyTool().execute(
        ctx,
        ClarifyArgs(
            question="请选择指标口径",
            options=[{"label": "总下单客户数", "value": "总下单客户数"}],
        ),
    )

    assert _succeeded(output)
    assert _data(output)["options"][0]["bindings"] == [
        {
            "subquery_id": "metric:1",
            "asset_type": "METRIC",
            "asset_id": 274,
            "model_id": 246,
        }
    ]


def test_semantic_clarification_rejects_unmapped_option():
    output = ClarifyTool().execute(
        _ambiguous_metric_clarification_context(),
        ClarifyArgs(
            question="请选择指标口径",
            options=[{"label": "未知口径", "value": "metric-999"}],
        ),
    )

    assert output.status == ToolStatus.REJECTED
    assert output.error_code == "semantic_clarification_option_invalid"


def test_semantic_clarification_corrects_model_generated_slot_id():
    output = ClarifyTool().execute(
        _ambiguous_metric_clarification_context(),
        ClarifyArgs(
            question="请选择指标口径",
            options=[
                {
                    "label": "总下单客户数",
                    "value": "总下单客户数",
                    "bindings": [
                        {
                            "subquery_id": "m1",
                            "asset_type": "METRIC",
                            "asset_id": 274,
                            "model_id": 246,
                        }
                    ],
                }
            ],
        ),
    )

    assert _succeeded(output)
    assert _data(output)["options"][0]["bindings"][0]["subquery_id"] == "metric:1"


def test_compile_does_not_repeat_question_understanding_gate():
    service = RecordingSemanticCompilationService()
    ctx = _ctx(
        dataset_id=3,
        semantic_asset_ids=[10],
        question_understanding={
            "rewritten_question": "看一下最近7天的数据",
            "intent": {"intent_type": "metric_query", "metric_mentions": []},
            "validation": {
                "status": "clarification_required",
                "clarification_slots": ["metric"],
            },
        },
    )

    output = CompileSemanticSqlTool(service, RecordingQueryService()).execute(
        ctx,
        CompileSemanticSqlArgs(metric_asset_ids=[10]),
    )

    assert _succeeded(output)
    assert len(service.calls) == 1


def test_compile_rejects_asset_outside_package():
    ctx = _ctx(dataset_id=3, semantic_asset_ids=[10, 11])
    output = CompileSemanticSqlTool(
        RecordingSemanticCompilationService(), RecordingQueryService()
    ).execute(
        ctx,
        CompileSemanticSqlArgs(metric_asset_ids=[10], dimension_asset_ids=[99]),
    )
    assert not _succeeded(output)
    assert output.error_code == "asset_not_in_package"
    assert "99" in output.model_content


def test_compile_uses_trusted_plan_instead_of_model_extra_assets():
    normalized_time = {
        "kind": "single_date",
        "anchor": "today",
        "offset_days": 0,
        "timezone": "Asia/Shanghai",
    }
    service = RecordingSemanticCompilationService()
    ctx = _ctx(
        dataset_id=3,
        semantic_asset_ids=[274, 278, 276],
        question_understanding={
            "rewritten_question": "今天店铺的客户数",
            "intent": {
                "intent_type": "metric_query",
                "metric_mentions": ["客户数"],
                "time_range": {
                    "raw": "今天",
                    "value_status": "provided",
                    "normalized": normalized_time,
                },
            },
            "validation": {"status": "valid"},
        },
    )
    ctx.state["semantic_scope"]["compile_plan"] = {
        "metric_asset_ids": [274],
        "dimension_asset_ids": [278],
        "filters": [],
        "temporal_plan": {
            "filters": [
                {
                    "asset_id": 276,
                    "operator": "=",
                    "value": normalized_time,
                }
            ],
            "time_bucket": None,
        },
    }

    output = CompileSemanticSqlTool(service, RecordingQueryService()).execute(
        ctx,
        CompileSemanticSqlArgs(
            metric_asset_ids=[274],
            dimension_asset_ids=[278, 277],
            filters=[
                {
                    "asset_id": 276,
                    "operator": "=",
                    "value": normalized_time,
                }
            ],
            time_bucket={
                "dimension_id": 276,
                "grain": "day",
            },
        ),
    )

    assert _succeeded(output)
    assert service.calls[0].time_bucket is None
    assert service.calls[0].slots == {
        "metrics": [{"asset_id": 274, "asset_type": "METRIC"}],
        "dimensions": [{"asset_id": 278, "asset_type": "DIMENSION"}],
        "filters": [
            {
                "asset_id": 276,
                "asset_type": "DIMENSION",
                "operator": "=",
                "value": normalized_time,
            }
        ],
    }


def test_project_compile_plan_covers_ranking_shape():
    plan = project_semantic_compile_plan(
        {
            "metrics": [{"asset_type": "METRIC", "asset_id": 100}],
            "group_dimensions": [{"asset_type": "DIMENSION", "asset_id": 200}],
            "dimension_filters": [],
            "time_filters": [],
        },
        {
            "intent_type": "ranking_analysis",
            "query_shape": {
                "needs_group_by": True,
                "needs_order_by": True,
                "order_direction": "asc",
                "limit": 5,
            },
        },
    )

    assert plan.model_dump(mode="json") == {
        "metric_asset_ids": [100],
        "dimension_asset_ids": [200],
        "filters": [],
        "temporal_plan": {
            "filters": [],
            "time_bucket": None,
        },
        "order_by": [{"asset_id": 100, "direction": "asc"}],
        "limit": 5,
        "intent_type": "ranking_analysis",
        "query_shape": {
            "needs_group_by": True,
            "needs_order_by": True,
            "order_direction": "asc",
            "limit": 5,
        },
    }


def test_project_compile_plan_builds_independent_trusted_temporal_plan():
    normalized_time = {
        "kind": "absolute_range",
        "start": "2026-06-01",
        "end_exclusive": "2026-07-01",
        "timezone": "Asia/Shanghai",
    }
    plan = project_semantic_compile_plan(
        {
            "metrics": [{"asset_type": "METRIC", "asset_id": 271}],
            "group_dimensions": [],
            "dimension_filters": [
                {
                    "asset_type": "DIMENSION",
                    "asset_id": 278,
                    "operator": "=",
                    "value": "100011",
                }
            ],
            "time_dimensions": [{"asset_type": "DIMENSION", "asset_id": 276}],
            "time_filters": [
                {
                    "asset_type": "DIMENSION",
                    "asset_id": 276,
                    "operator": "=",
                    "value": normalized_time,
                }
            ],
        },
        {
            "intent_type": "trend_analysis",
            "query_shape": {
                "needs_group_by": True,
                "time_grain": "day",
            },
        },
    )

    assert plan.model_dump(mode="json") == {
        "metric_asset_ids": [271],
        "dimension_asset_ids": [],
        "filters": [
            {
                "asset_id": 278,
                "operator": "=",
                "value": "100011",
            }
        ],
        "temporal_plan": {
            "filters": [
                {
                    "asset_id": 276,
                    "operator": "=",
                    "value": normalized_time,
                }
            ],
            "time_bucket": {
                "dimension_id": 276,
                "grain": "day",
            },
        },
        "order_by": [],
        "limit": None,
        "intent_type": "trend_analysis",
        "query_shape": {
            "needs_group_by": True,
            "time_grain": "day",
        },
    }


def test_compile_uses_server_time_bucket_for_trend_query():
    normalized_time = {
        "kind": "absolute_range",
        "start": "2026-06-01",
        "end_exclusive": "2026-07-01",
        "timezone": "Asia/Shanghai",
    }
    service = RecordingSemanticCompilationService()
    ctx = _ctx(
        dataset_id=3,
        semantic_asset_ids=[271, 278, 276],
        question_understanding={
            "rewritten_question": "2026年6月店铺100011总GMV按天趋势",
            "intent": {
                "intent_type": "trend_analysis",
                "metric_mentions": ["总GMV"],
                "time_range": {
                    "raw": "2026年6月",
                    "value_status": "provided",
                    "normalized": normalized_time,
                },
            },
            "validation": {"status": "valid"},
        },
    )
    ctx.state["semantic_scope"]["compile_plan"] = {
        "metric_asset_ids": [271],
        "dimension_asset_ids": [],
        "filters": [
            {
                "asset_id": 278,
                "operator": "=",
                "value": "100011",
            }
        ],
        "temporal_plan": {
            "filters": [
                {
                    "asset_id": 276,
                    "operator": "=",
                    "value": normalized_time,
                }
            ],
            "time_bucket": {
                "dimension_id": 276,
                "grain": "day",
            },
        },
        "intent_type": "trend_analysis",
        "query_shape": {
            "needs_group_by": True,
            "time_grain": "day",
        },
    }

    output = CompileSemanticSqlTool(service, RecordingQueryService()).execute(
        ctx,
        CompileSemanticSqlArgs(
            metric_asset_ids=[271],
            time_bucket={
                "start": "2026-06-01",
                "end": "2026-07-01",
                "granularity": "day",
            },
        ),
    )

    assert _succeeded(output)
    assert service.calls[0].time_bucket == {
        "dimension_id": 276,
        "grain": "day",
    }
    assert service.calls[0].slots["filters"] == [
        {
            "asset_id": 278,
            "asset_type": "DIMENSION",
            "operator": "=",
            "value": "100011",
        },
        {
            "asset_id": 276,
            "asset_type": "DIMENSION",
            "operator": "=",
            "value": normalized_time,
        },
    ]


def test_compile_rejects_incomplete_ranking_plan_before_compilation():
    service = RecordingSemanticCompilationService()
    ctx = _ctx(dataset_id=3, semantic_asset_ids=[100, 200])
    ctx.state["semantic_scope"]["compile_plan"] = {
        "metric_asset_ids": [100],
        "dimension_asset_ids": [],
        "filters": [],
        "order_by": [],
        "limit": None,
        "intent_type": "ranking_analysis",
        "query_shape": {
            "needs_group_by": True,
            "needs_order_by": True,
        },
    }

    output = CompileSemanticSqlTool(service, RecordingQueryService()).execute(
        ctx,
        CompileSemanticSqlArgs(metric_asset_ids=[100]),
    )

    assert output.status == ToolStatus.REJECTED
    assert output.error_code == "semantic_query_plan_incomplete"
    assert service.calls == []
    assert output.details == {
        "intent_type": "ranking_analysis",
        "missing_requirements": [
            "group_dimension_asset_ids",
            "order_by",
            "limit",
        ],
        "query_shape": {
            "needs_group_by": True,
            "needs_order_by": True,
        },
    }


def test_compile_rejects_sql_that_does_not_cover_ranking_plan():
    service = IncompleteSemanticCompilationService()
    ctx = _ctx(dataset_id=3, semantic_asset_ids=[100, 200])
    ctx.state["semantic_scope"]["compile_plan"] = {
        "metric_asset_ids": [100],
        "dimension_asset_ids": [200],
        "filters": [],
        "order_by": [{"asset_id": 100, "direction": "desc"}],
        "limit": 5,
        "intent_type": "ranking_analysis",
        "query_shape": {
            "needs_group_by": True,
            "needs_order_by": True,
            "limit": 5,
        },
    }

    output = CompileSemanticSqlTool(service, RecordingQueryService()).execute(
        ctx,
        CompileSemanticSqlArgs(metric_asset_ids=[100]),
    )

    assert output.status == ToolStatus.REJECTED
    assert output.error_code == "compiled_query_plan_not_covered"
    assert output.details == {
        "missing_requirements": ["group_by", "order_by", "limit:5"],
        "compiled_sql": "select metric_value, dimension_value from t",
    }


def test_compile_rejects_scope_from_another_workspace():
    ctx = _ctx(dataset_id=3, semantic_asset_ids=[10])
    ctx.state["semantic_scope"]["workspace_id"] = 2

    output = CompileSemanticSqlTool(
        RecordingSemanticCompilationService(), RecordingQueryService()
    ).execute(ctx, CompileSemanticSqlArgs(metric_asset_ids=[10]))

    assert output.status == ToolStatus.REJECTED
    assert output.error_code == "semantic_scope_mismatch"


def test_compile_rechecks_tables_after_permissions_change():
    ctx = _ctx(dataset_id=3, semantic_asset_ids=[10])
    ctx.state["semantic_scope"]["authorized_tables"] = ["secret_orders"]

    output = CompileSemanticSqlTool(
        RecordingSemanticCompilationService(), RecordingQueryService()
    ).execute(ctx, CompileSemanticSqlArgs(metric_asset_ids=[10]))

    assert output.status == ToolStatus.REJECTED
    assert output.error_code == "semantic_scope_permission_changed"


def test_compile_passes_known_assets_to_capability():
    service = RecordingSemanticCompilationService()
    ctx = _ctx(
        dataset_id=3,
        semantic_asset_ids=[10, 11],
    )
    output = CompileSemanticSqlTool(service, RecordingQueryService()).execute(
        ctx, CompileSemanticSqlArgs(metric_asset_ids=[10], dimension_asset_ids=[11])
    )
    assert _succeeded(output)
    slots = service.calls[0].slots
    assert slots["metrics"] == [{"asset_id": 10, "asset_type": "METRIC"}]
    assert slots["dimensions"] == [{"asset_id": 11, "asset_type": "DIMENSION"}]
    assert "compiled_sql" not in ctx.state
    projection = ChatBIToolResultProcessor().process(
        ctx,
        "compile_semantic_sql",
        output,
    )
    assert projection.state_patch["compiled_sql"] == "select 1"
    assert "t" in projection.state_patch["allowed_tables"]


def test_compile_rejects_raw_time_literal_even_when_legacy_range_matches():
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

    output = CompileSemanticSqlTool(service, RecordingQueryService()).execute(
        ctx,
        CompileSemanticSqlArgs(
            metric_asset_ids=[10],
            filters=[{"asset_id": 11, "operator": "=", "value": "today"}],
        ),
    )

    assert not _succeeded(output)
    assert output.error_code == "time_filter_mismatch"
    assert service.calls == []


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

    output = CompileSemanticSqlTool(
        RecordingSemanticCompilationService(), RecordingQueryService()
    ).execute(
        ctx,
        CompileSemanticSqlArgs(
            metric_asset_ids=[10],
            filters=[{"asset_id": 11, "operator": "=", "value": "2026-06-30"}],
        ),
    )

    assert not _succeeded(output)
    assert output.error_code == "time_filter_mismatch"
    assert "禁止省略时间或替换成数据最大日期" in output.model_content


def test_search_result_processor_collects_asset_ids_and_tables():
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
        "dataset_id": 3,
        "tables": ["dws_sales"],
        "candidate_groups": {
            "metrics": [
                {"asset_type": "METRIC", "asset_id": 7, "biz_name": "gmv"},
                {
                    "asset_type": "METRIC",
                    "asset_id": 9,
                    "biz_name": "candidate_only",
                    "internal": "hidden",
                },
            ]
        },
        "selected_assets": {
            "metrics": [{"asset_type": "METRIC", "asset_id": 7, "biz_name": "gmv"}],
            "dimensions": [
                {
                    "asset_type": "DIMENSION",
                    "asset_id": 8,
                    "biz_name": "city",
                }
            ],
        },
        "slot_bindings": {
            "metrics": [{"asset_type": "METRIC", "asset_id": 7}],
            "group_dimensions": [{"asset_type": "DIMENSION", "asset_id": 8}],
            "dimension_filters": [],
            "time_filters": [],
        },
        "allowed_asset_ids": [
            {"asset_type": "METRIC", "asset_id": 7},
            {"asset_type": "DIMENSION", "asset_id": 8},
        ],
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
    assert request.intent.metric_mentions == ["gmv"]
    assert "semantic_asset_ids" not in ctx.state
    projection = ChatBIToolResultProcessor().process(
        ctx,
        "search_semantic_assets",
        output,
    )
    assert projection.state_patch["semantic_asset_ids"] == [7, 8]
    assert projection.state_patch["allowed_tables"] == ["dws_sales"]
    assert projection.state_patch["semantic_package"]["dataset_id"] == 3
    assert (
        "internal"
        not in projection.state_patch["semantic_package"]["candidate_groups"][
            "metrics"
        ][1]
    )
    assert projection.state_patch["semantic_scope"]["retrieval_id"]
    assert projection.state_patch["semantic_scope"]["compile_plan"] == {
        "metric_asset_ids": [7],
        "dimension_asset_ids": [8],
        "filters": [],
        "temporal_plan": {
            "filters": [],
            "time_bucket": None,
        },
        "order_by": [],
        "limit": None,
        "intent_type": "metric_query",
        "query_shape": {},
    }


def test_search_maps_dimension_ambiguity_for_agent_flow():
    package = {
        "status": "metric_ambiguous",
        "dataset_id": 3,
        "tables": ["dws_sales"],
        "decision": {"status": "ambiguous"},
        "ambiguities": [{"type": "dimension"}],
    }
    output = SearchSemanticAssetsTool(
        RecordingSemanticRetrievalService(package),
        RecordingQueryService(),
    ).execute(_ctx(dataset_id=3), SearchSemanticAssetsArgs())

    assert _succeeded(output)
    assert _data(output)["package"]["status"] == "dimension_ambiguous"


def test_search_reports_missing_time_dimension_configuration():
    package = {
        "status": "missed",
        "dataset_id": 3,
        "tables": ["dws_sales"],
        "decision": {
            "status": "partial",
            "reason_codes": ["TIME_DIMENSION_NOT_CONFIGURED_FOR_METRIC_MODEL"],
        },
    }
    output = SearchSemanticAssetsTool(
        RecordingSemanticRetrievalService(package),
        RecordingQueryService(),
    ).execute(_ctx(dataset_id=3), SearchSemanticAssetsArgs())

    assert _succeeded(output)
    assert _data(output)["package"]["status"] == "time_dimension_not_configured"


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
    assert output.error_code == "semantic_retrieval_request_required"
