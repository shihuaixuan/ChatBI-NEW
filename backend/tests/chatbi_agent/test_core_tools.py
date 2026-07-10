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
    return AgentToolContext(session=None, oid=1, user_id=1, datasource_id=5, state=dict(state))


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


def test_search_collects_asset_ids_and_tables_into_state():
    ctx = _ctx(dataset_id=3)
    package = {
        "hit": True,
        "status": "hit",
        "tables": ["dws_sales"],
        "candidate_groups": {"metrics": [{"asset_id": 7, "biz_name": "gmv"}]},
        "selected_assets": {"dimensions": [{"asset_id": 8, "biz_name": "city"}]},
    }
    with patch("apps.chatbi_agent.tools.core.retrieve_semantic_assets", return_value=package):
        output = SearchSemanticAssetsTool().execute(ctx, SearchSemanticAssetsArgs(question="按城市看 gmv"))
    assert output.success
    assert ctx.state["semantic_asset_ids"] == [7, 8]
    assert ctx.state["allowed_tables"] == ["dws_sales"]
    assert ctx.state["semantic_package"] is package
