"""P0-3 死角修补回归：理解校验失败、STRICT 非 PROVEN、finish 失败都不再死循环。"""

from __future__ import annotations

from typing import Any

from apps.chatbi.errors import AgentFinalizationError
from apps.chatbi.models import AgentErrorClass
from apps.chatbi.orchestration.agent.messages import AgentMessage
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tool_execution import _bounded_summary
from apps.chatbi.orchestration.agent.tool_visibility import visible_tool_names
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.orchestration.agent.tools.core import FinishTool
from apps.chatbi.services.generation.agent_finalization import (
    AgentFinalizationInput,
    AgentFinalizationResult,
    build_partial_finalization,
)
from apps.tool import BudgetGuard, ToolStatus

REGISTERED_TOOLS = [
    "parse_time_range",
    "search_semantic_assets",
    "search_terminology",
    "get_sql_examples",
    "get_dataset_schema",
    "compile_semantic_sql",
    "validate_sql",
    "execute_sql",
    "clarify",
    "finish",
]


def _state() -> AgentRuntimeState:
    state = AgentRuntimeState(
        run=type("Run", (), {"id": 1, "oid": 1, "chat_id": 1})(),
        record=type("Record", (), {"id": 1})(),
        context=AgentToolContext(session=None, oid=1, user_id=1, datasource_id=1),
        messages=[AgentMessage.user("按城市看 gmv")],
        budget=BudgetGuard(max_steps=5),
        system=AgentMessage.system("系统提示词"),
    )
    state.context.state["question_understanding"] = {
        "validation": {"status": "valid"},
        "category": "data_query",
    }
    return state


def test_invalid_validation_exposes_clarify_instead_of_nothing():
    state = _state()
    state.context.state["question_understanding"] = {
        "validation": {"status": "clarification_required", "reason_codes": ["intent_unknown"]},
        "category": "data_query",
    }
    assert visible_tool_names(state, "normal", REGISTERED_TOOLS) == ["clarify"]


def test_strict_unproven_plan_exposes_clarify_instead_of_nothing():
    state = _state()
    state.context.state["semantic_scope"] = {
        "semantic_enforcement": "STRICT",
        "decision_status": "resolved",
        "query_plan": {"validation_status": "NOT_PROVEN"},
        "validation_report": {"status": "NOT_PROVEN"},
    }
    assert visible_tool_names(state, "normal", REGISTERED_TOOLS) == ["clarify"]


def test_parse_time_range_revisible_when_scope_lacks_time():
    state = _state()
    state.context.state["semantic_scope"] = {
        "decision_status": "degraded",
    }
    visible = visible_tool_names(state, "normal", REGISTERED_TOOLS)
    assert visible[0] == "parse_time_range"

    # 时间已解析后不再重复暴露时间工具。
    state.context.state["time_range"] = {"kind": "absolute"}
    visible = visible_tool_names(state, "normal", REGISTERED_TOOLS)
    assert "parse_time_range" not in visible


class _FailingFinalization:
    def generate(self, data: AgentFinalizationInput) -> AgentFinalizationResult:
        raise AgentFinalizationError(
            "AGENT_CHART_FIELD_INVALID",
            "图表配置使用了查询结果之外的字段。",
        )


class _Ctx:
    """FinishTool 所需的最小执行上下文。"""

    def __init__(self, state: dict[str, Any]):
        self.state = state
        self.oid = 1
        self.user_id = 1


def test_finish_degrades_to_partial_answer_on_finalization_error():
    tool = FinishTool(_FailingFinalization())
    state = {
        "question": "本月销售额",
        "question_understanding": {"intent": {"intent_type": "metric_query"}},
        "last_execution": {
            "sql": "select 1",
            "fields": ["region", "sales"],
            "row_count": 2,
            "status": "succeeded",
        },
        "full_data": [
            {"region": "华东", "sales": 100},
            {"region": "华北", "sales": 80},
        ],
    }
    result = tool.execute(_Ctx(state), args=type("Args", (), {})())
    assert result.status == ToolStatus.SUCCEEDED
    assert result.data is not None
    assert result.data.chart == {}
    assert result.data.sql == "select 1"
    assert "AGENT_CHART_FIELD_INVALID" in result.data.answer
    assert "华东" in result.data.answer


def test_build_partial_finalization_keeps_real_numbers_only():
    partial = build_partial_finalization(
        execution={"sql": "select 1", "fields": ["region", "sales"], "row_count": 2},
        rows=[{"region": "华东", "sales": 100}, {"region": "华北", "sales": 80}],
        failed_stage="agent_answer_generation",
    )
    assert partial.chart == {}
    assert "行数：2" in partial.answer
    assert "region、sales" in partial.answer
    assert "华东" in partial.answer and "华北" in partial.answer


def test_bounded_summary_keeps_parseable_structure_when_truncating():
    rows = [{"region": f"区域{i}", "sales": i, "extra": "x" * 400} for i in range(50)]
    payload = {
        "fields": ["region", "sales", "extra"],
        "rows": rows,
        "row_count": 50,
    }
    summary = _bounded_summary(payload)
    import orjson

    encoded = orjson.dumps(summary).decode()
    assert len(encoded) <= 2000
    # 结构仍然可解析，schema 与统计保留，而不是半截 JSON 字符串。
    assert summary["fields"] == ["region", "sales", "extra"]
    assert summary["row_count"] == 50
    assert isinstance(summary["rows"], list) and summary["rows"]
    assert summary["_truncated"] is True


def test_bounded_summary_unchanged_when_small():
    payload = {"fields": ["a"], "row_count": 1}
    assert _bounded_summary(payload) == payload


def test_agent_error_class_extended_for_failure_attribution():
    assert AgentErrorClass.BINDING_AMBIGUOUS.value == "binding_ambiguous"
    assert AgentErrorClass.PLAN_INVALID.value == "plan_invalid"
    assert AgentErrorClass.FINALIZE.value == "finalize_failed"
