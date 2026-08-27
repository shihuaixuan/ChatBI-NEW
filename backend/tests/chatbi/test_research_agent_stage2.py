"""阶段 2 的 Agent 输入、上下文投影和决策解析测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from apps.chatbi.models.dto.research_agent import (
    AttemptSummary,
    Evidence,
    EvidenceColumn,
    EvidenceData,
    EvidenceDefinition,
    ExecutionError,
    Finding,
    FindingScope,
    RemainingBudget,
    ResearchActionType,
    ResearchExecutionErrorStage,
    ResearchState,
    SemanticContext,
    SemanticDimension,
    SemanticHierarchy,
    SemanticMetric,
    TodoItem,
)
from apps.chatbi.orchestration.agent.messages import AgentMessage
from apps.chatbi.orchestration.agent.reasoning import (
    AgentDecision,
    AgentReasoner,
    ResearchDecisionParseError,
    parse_research_turn_decision,
)
from apps.chatbi.orchestration.agent.reasoning_profile import (
    RESEARCH_REACT_PROFILE,
)
from apps.chatbi.services.research.agent_context import (
    build_research_agent_input,
    build_research_agent_input_from_requirement,
    project_research_react_state,
    serialize_semantic_context_yaml,
)
from apps.tool import ToolCall
from tests.chatbi.test_research_agent_contracts import _governed_requirement


def _semantic_context(metric_count: int = 1) -> SemanticContext:
    return SemanticContext(
        metrics=tuple(
            SemanticMetric(
                ref=f"METRIC:{index}:1",
                name=f"指标 {index}",
                description="长" * 1_000,
                aggregation="SUM",
            )
            for index in range(metric_count)
        ),
        dimensions=(
            SemanticDimension(
                ref="DIMENSION:20:1",
                name="区域",
                description="区域维度",
            ),
        ),
    )


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="evidence:call-1",
        evidence_type="query_result",
        purpose="查询指标",
        definition=EvidenceDefinition(metrics=("METRIC:0:1",)),
        columns=(
            EvidenceColumn(
                name="value",
                semantic_ref="METRIC:0:1",
                role="metric",
                data_type="decimal",
            ),
        ),
        data=EvidenceData(
            row_count=25,
            rows=tuple((index,) for index in range(25)),
        ),
    )


def _decision(action_name: str, arguments: dict[str, object]) -> AgentDecision:
    call = ToolCall(
        name=action_name,
        args=arguments,
        call_id="call-1",
    )
    return AgentDecision(
        response=AgentMessage.assistant("", tool_calls=[call]),
        reasoning="",
        tool_calls=[call],
        usage={},
    )


def test_input_builder_normalizes_messages_and_keeps_snapshot_frozen() -> None:
    value = build_research_agent_input(
        "分析 GMV",
        _semantic_context(),
        conversation_context=({"role": "user", "content": "请继续"},),
        agent_input_ref="input-1",
    )

    assert value.conversation_context[0].content == "请继续"
    with pytest.raises(ValidationError):
        value.user_question = "被修改的问题"  # type: ignore[misc]


def test_input_builder_accepts_governed_hierarchy_ref_and_rejects_foreign_asset() -> None:
    requirement = _governed_requirement()
    context = SemanticContext(
        metrics=(
            SemanticMetric(
                ref="METRIC:10:1",
                name="GMV",
                aggregation="SUM",
            ),
        ),
        dimensions=tuple(
            SemanticDimension(ref=ref, name=ref, description="维度")
            for ref in requirement.scope.dimension_refs
        ),
        hierarchies=(
            SemanticHierarchy(
                ref="HIERARCHY:geo:published",
                name="地理层级",
                levels=requirement.scope.hierarchies[0].dimension_refs,
            ),
        ),
    )

    value = build_research_agent_input_from_requirement(
        requirement,
        user_question="分析 GMV",
        semantic_context=context,
        agent_input_ref="input-1",
    )
    assert value.semantic_context.hierarchies[0].ref == "HIERARCHY:geo:published"

    foreign_context = SemanticContext(
        metrics=(
            SemanticMetric(
                ref="METRIC:999:1",
                name="未授权指标",
                aggregation="SUM",
            ),
        ),
    )
    with pytest.raises(
        ValueError,
        match="RESEARCH_AGENT_INPUT_SEMANTIC_REF_OUT_OF_SCOPE",
    ):
        build_research_agent_input_from_requirement(
            requirement,
            user_question="分析 GMV",
            semantic_context=foreign_context,
            agent_input_ref="input-1",
        )


def test_semantic_context_yaml_is_stable_and_bounded() -> None:
    context = _semantic_context(metric_count=45)
    first = serialize_semantic_context_yaml(context, max_chars=100_000)
    second = serialize_semantic_context_yaml(context, max_chars=100_000)

    assert first == second
    payload = yaml.safe_load(first)
    assert len(payload["semantic_context"]["metrics"]) == 40
    assert len(payload["semantic_context"]["metrics"][0]["description"]) == 1_000
    assert list(payload["semantic_context"]) == [
        "metrics",
        "dimensions",
        "hierarchies",
        "metric_formulas",
        "metric_analysis_relations",
        "ambiguities",
    ]


def test_semantic_context_yaml_clips_to_total_character_budget() -> None:
    serialized = serialize_semantic_context_yaml(
        _semantic_context(metric_count=45),
        max_chars=4_000,
    )

    assert len(serialized) <= 4_000


def test_react_projection_hides_superseded_and_completed_state() -> None:
    evidence = _evidence()
    state = ResearchState(
        agent_input_ref="input-1",
        evidence_refs=(evidence.evidence_id,),
        findings=(
            Finding(
                finding_id="finding-current",
                statement="当前结论",
                evidence_ids=(evidence.evidence_id,),
                scope=FindingScope(metric_refs=("METRIC:0:1",)),
            ),
            Finding(
                finding_id="finding-old",
                statement="旧结论",
                evidence_ids=(evidence.evidence_id,),
                scope=FindingScope(metric_refs=("METRIC:0:1",)),
                status="superseded",
            ),
        ),
        todo_items=(
            TodoItem(todo_id="todo-open", goal="继续分析", status="pending", order=1),
            TodoItem(
                todo_id="todo-done",
                goal="已完成分析",
                status="completed",
                order=2,
                result_reason="已有证据支持",
            ),
        ),
        attempted_actions=(
            AttemptSummary(
                attempt_id="attempt-1",
                action_type=ResearchActionType.QUERY_SEMANTIC_DATA,
                purpose="查询指标",
                action_fingerprint="same-query",
                status="failed",
                error=ExecutionError(
                    code="QUERY_FAILED",
                    stage=ResearchExecutionErrorStage.EXECUTION,
                    message="第一次失败",
                ),
            ),
            AttemptSummary(
                attempt_id="attempt-2",
                action_type=ResearchActionType.QUERY_SEMANTIC_DATA,
                purpose="查询指标",
                action_fingerprint="same-query",
                status="failed",
                error=ExecutionError(
                    code="QUERY_FAILED",
                    stage=ResearchExecutionErrorStage.EXECUTION,
                    message="第二次失败",
                ),
            ),
        ),
    )
    projected = project_research_react_state(
        build_research_agent_input("分析 GMV", _semantic_context(), agent_input_ref="input-1"),
        state,
        evidence=(evidence,),
        remaining_budget=RemainingBudget(
            model_turns=3,
            query_calls=2,
            compute_calls=1,
            semantic_search_calls=1,
            wall_time_ms=10_000,
            query_cost=5,
        ),
    )

    assert projected["research_agent_input"]["semantic_context"].startswith(
        "semantic_context:\n"
    )
    assert [item["finding_id"] for item in projected["findings"]] == [
        "finding-current"
    ]
    assert [item["todo_id"] for item in projected["todo_items"]] == ["todo-open"]
    assert projected["evidence"][0]["data"]["rows"] == [
        [index] for index in range(20)
    ]
    assert projected["attempted_actions"][0]["repeat_count"] == 2
    assert projected["attempted_actions"][0]["error"]["message"] == "第二次失败"


def test_tool_call_is_parsed_as_research_turn_decision() -> None:
    decision = parse_research_turn_decision(
        _decision(
            "query_semantic_data",
            {
                "purpose": "查询 GMV",
                "metrics": ["METRIC:0:1"],
                "result": {"limit": 20},
            },
        ),
        available_tools=("query_semantic_data",),
    )

    assert decision.actions[0].action_type is ResearchActionType.QUERY_SEMANTIC_DATA
    assert decision.actions[0].arguments.metrics == ("METRIC:0:1",)


def test_json_decision_and_visible_tool_boundary_are_validated() -> None:
    content = (
        '{"actions":[{"action_type":"query_semantic_data",'
        '"purpose":"查询 GMV","arguments":{"metrics":["METRIC:0:1"],'
        '"result":{"limit":20}}}]}'
    )
    decision = parse_research_turn_decision(
        AgentDecision(
            response=AgentMessage.assistant(content),
            reasoning=content,
            tool_calls=[],
            usage={},
        ),
        available_tools=("query_semantic_data",),
    )
    assert decision.actions[0].action_type.value == "query_semantic_data"

    with pytest.raises(ResearchDecisionParseError) as error:
        parse_research_turn_decision(
            _decision("finish_research", {"completion": {}}),
            available_tools=("query_semantic_data",),
        )
    assert error.value.code == "RESEARCH_AGENT_DECISION_TOOL_NOT_VISIBLE"


def test_research_react_profile_has_prompt_and_six_tools() -> None:
    assert RESEARCH_REACT_PROFILE.prompt_version == "research-react-v1"
    assert RESEARCH_REACT_PROFILE.system_prompt is not None
    assert "plan_execution_state" not in RESEARCH_REACT_PROFILE.system_prompt
    assert RESEARCH_REACT_PROFILE.fixed_tool_allowlist == (
        "query_semantic_data",
        "compute_evidence",
        "read_evidence_rows",
        "search_semantic_assets",
        "request_clarification",
        "finish_research",
    )


def test_research_react_profile_prompt_is_used_as_system_message() -> None:
    state = SimpleNamespace(
        system=AgentMessage.system("旧 Research 系统提示词"),
        messages=[],
        runtime_context=None,
    )

    messages = AgentReasoner._invoke_messages(
        object(),
        state,
        RESEARCH_REACT_PROFILE,
        [],
        {"evidence": []},
    )

    assert messages[0].content == RESEARCH_REACT_PROFILE.system_prompt
    assert messages[0].content != "旧 Research 系统提示词"
    assert "search_semantic_assets" in messages[0].content
    assert "unanswerable" in messages[0].content
