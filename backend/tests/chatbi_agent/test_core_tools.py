"""核心工具的守护行为测试（不依赖真实 DB/LLM）。"""

from unittest.mock import patch

from apps.chatbi_agent.tools.base import AgentToolContext
from apps.chatbi_agent.tools.core import (
    CompileSemanticSqlArgs,
    CompileSemanticSqlTool,
    FinishArgs,
    FinishTool,
    SearchSemanticAssetsArgs,
    SearchSemanticAssetsTool,
)
from apps.chatbi_capabilities.schemas import ToolResult


def _ctx(**state):
    values = {
        "question_understanding": {
            "rewritten_question": "本月销售额",
            "intent": {"intent_type": "metric_query", "metric_mentions": ["销售额"]},
            "validation": {"status": "valid"},
        }
    }
    values.update(state)
    return AgentToolContext(session=None, oid=1, user_id=1, datasource_id=5, state=values)


def test_finish_rejected_without_execution():
    output = FinishTool().execute(_ctx(), FinishArgs(answer_markdown="答案"))
    assert not output.success
    assert output.error_code == "execution_required_before_finish"


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
    ctx = _ctx(dataset_id=3, semantic_asset_ids=[10, 11])
    with patch("apps.chatbi_agent.tools.core.compile_semantic_sql") as compile_mock:
        compile_mock.return_value = ToolResult(success=True, payload={"sql": "select 1", "tables": ["t"]})
        output = CompileSemanticSqlTool().execute(
            ctx, CompileSemanticSqlArgs(metric_asset_ids=[10], dimension_asset_ids=[11])
        )
    assert output.success
    slots = compile_mock.call_args.kwargs["slots"]
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

    with patch("apps.chatbi_agent.tools.core.compile_semantic_sql") as compile_mock:
        compile_mock.return_value = ToolResult(success=True, payload={"sql": "select 1", "tables": ["t"]})
        output = CompileSemanticSqlTool().execute(
            ctx,
            CompileSemanticSqlArgs(
                metric_asset_ids=[10],
                filters=[{"asset_id": 11, "operator": "=", "value": "today"}],
            ),
        )

    assert output.success
    assert compile_mock.call_args.kwargs["slots"]["filters"] == [
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
    ctx = _ctx(
        dataset_id=3,
        question_understanding={
            "rewritten_question": "按城市看 gmv",
            "intent": intent,
            "validation": {"status": "valid"},
        },
    )
    package = {
        "hit": True,
        "status": "hit",
        "tables": ["dws_sales"],
        "candidate_groups": {"metrics": [{"asset_id": 7, "biz_name": "gmv"}]},
        "selected_assets": {"dimensions": [{"asset_id": 8, "biz_name": "city"}]},
    }
    with patch("apps.chatbi_agent.tools.core.retrieve_semantic_assets", return_value=package) as retrieve_mock:
        output = SearchSemanticAssetsTool().execute(ctx, SearchSemanticAssetsArgs())
    assert output.success
    assert retrieve_mock.call_args.kwargs["question"] == "按城市看 gmv"
    assert retrieve_mock.call_args.kwargs["intent"] is intent
    assert ctx.state["semantic_asset_ids"] == [7, 8]
    assert ctx.state["allowed_tables"] == ["dws_sales"]
    assert ctx.state["semantic_package"] is package


def test_search_rejects_missing_confirmed_understanding():
    output = SearchSemanticAssetsTool().execute(
        _ctx(dataset_id=3, question_understanding=None),
        SearchSemanticAssetsArgs(),
    )

    assert not output.success
    assert output.error_code == "question_understanding_required"
