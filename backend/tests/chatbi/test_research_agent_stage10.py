"""阶段 10：Research ReAct 主路径、Trace 和离线评测验收。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.models.dto.research_agent import ResearchStateSnapshot
from apps.chatbi.orchestration.pipeline.research_agent_runtime import (
    ResearchAgentRuntime,
)
from apps.chatbi.services.computation.engine import ComputeEngine
from apps.chatbi.services.research.tools import build_research_tool_registry
from apps.trace import AgentTraceRecorder, DisabledTraceExporter, TraceNodeStatus
from tests.chatbi.research_agent_fixtures import (
    FakeSession,
    _governed_requirement,
    _run_row,
    _store,
)
from tests.chatbi.test_research_agent_stage7 import (
    _compute_difference_args,
    _finish_args,
    _query_args_for_period,
    _ScriptedClient,
    _SuccessfulSemanticRuntime,
)
from tests.trace.test_trace_recorder import RecordingRepository

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _traced_runtime() -> tuple[ResearchAgentRuntime, FakeSession, object, RecordingRepository]:
    """构造带内存 Trace 仓储的最小 Research 主路径。"""

    client = _ScriptedClient(
        [
            (
                "query_semantic_data",
                _query_args_for_period("current", "2026-08-01", "2026-08-31"),
                "query-current",
            ),
            (
                "query_semantic_data",
                _query_args_for_period("previous", "2026-07-01", "2026-07-31"),
                "query-previous",
            ),
            (
                "compute_evidence",
                _compute_difference_args(
                    ["evidence:query-current", "evidence:query-previous"]
                ),
                "compute-difference",
            ),
            (
                "finish_research",
                _finish_args(["evidence:compute-difference"]),
                "finish-research",
            ),
        ]
    )
    session = FakeSession(_run_row())
    requirement = _governed_requirement()
    repository = RecordingRepository()
    runtime = ResearchAgentRuntime(
        session=session,
        config=AgentConfig(enabled=True, tool_parallel_workers=2),
        run_row=session.run,
        record=SimpleNamespace(dataset_id=1, question="分析 GMV"),
        model_client=client,
        semantic_runtime=_SuccessfulSemanticRuntime(),
        compute_engine=ComputeEngine(),
        result_store=_store(),
        recorder=AgentTraceRecorder(repository, DisabledTraceExporter()),
    )
    return runtime, session, requirement, repository


def test_research_registry_and_state_have_no_legacy_plan_protocol() -> None:
    """生产 Research 注册表和持久化状态不得回到旧计划协议。"""

    registry = build_research_tool_registry()
    assert "submit_research_plan" not in registry.names()
    assert set(registry.names()) == {
        "query_semantic_data",
        "compute_evidence",
        "read_evidence_rows",
        "search_semantic_assets",
        "request_clarification",
        "finish_research",
    }

    runtime, session, requirement, _repository = _traced_runtime()
    outcome = runtime.run(requirement=requirement)
    assert outcome.completion is not None
    serialized = json.dumps(session.run.derived_state, ensure_ascii=False)
    assert "plan_execution_state" not in serialized
    snapshot = ResearchStateSnapshot.model_validate(
        session.run.derived_state["research_state_snapshot"]
    )
    assert "plan_execution_state" not in json.dumps(
        snapshot.model_dump(mode="json"), ensure_ascii=False
    )

    pipeline_source = Path(
        "apps/chatbi/orchestration/pipeline/research_agent_pipeline.py"
    ).read_text(encoding="utf-8")
    assert "plan_and_solve_runtime" not in pipeline_source


def test_research_trace_contains_decision_actions_evidence_budget_and_terminal() -> None:
    """一次完整运行必须留下阶段 10 要求的关键 Trace 事实。"""

    runtime, session, requirement, repository = _traced_runtime()
    outcome = runtime.run(requirement=requirement)

    assert outcome.stop_reason == "finished"
    names = {item.name for item in repository.started}
    assert {
        "prepare_reasoning_context",
        "research_turn_decision",
        "research_action_batch",
        "research_tool_result",
        "research_terminal",
    } <= names

    indexed = dict(enumerate(repository.started, start=1))
    finished = {item.node_id: item for item in repository.finished}

    context_node = next(item for item in indexed.values() if item.name == "prepare_reasoning_context")
    assert context_node.input_summary["prompt_version"] == "research-react-v1"
    assert context_node.input_summary["mode"] == "research_react"
    context_finish = finished[next(index for index, item in indexed.items() if item is context_node)]
    assert "query_semantic_data" in context_finish.output_summary["available_tools"]

    decision_node_id = next(
        index for index, item in indexed.items() if item.name == "research_turn_decision"
    )
    decision_node = indexed[decision_node_id]
    assert decision_node.metadata["prompt_version"] == "research-react-v1"
    assert finished[decision_node_id].output_summary["action_count"] == 1

    batch_node_id = next(
        index for index, item in indexed.items() if item.name == "research_action_batch"
    )
    batch_finish = finished[batch_node_id]
    assert batch_finish.state_diff["before"]["budget_usage"] != batch_finish.state_diff["after"]["budget_usage"]

    result_finishes = [
        finished[index]
        for index, item in indexed.items()
        if item.name == "research_tool_result"
    ]
    assert result_finishes
    assert all(item.output_summary["action_fingerprint"] for item in result_finishes)
    compute_result = next(
        item
        for item in result_finishes
        if item.output_summary["tool_name"] == "compute_evidence"
    )
    assert compute_result.output_summary["parent_evidence_ids"] == [
        "evidence:query-current",
        "evidence:query-previous",
    ]
    assert compute_result.output_summary["status"] == "succeeded"

    terminal_id = next(
        index for index, item in indexed.items() if item.name == "research_terminal"
    )
    terminal = finished[terminal_id]
    assert terminal.status is TraceNodeStatus.SUCCEEDED
    assert terminal.output_summary["status"] == "completed"
    assert terminal.output_summary["reason"] == "finish_research_accepted"
    assert session.run.derived_state["research_prompt_version"] == "research-react-v1"


def test_offline_eval_view_and_aggregate_metrics_are_computable() -> None:
    """新 ResearchState 可以生成离线评测视图和聚合指标。"""

    import run_research_agent_eval as evaluator

    runtime, session, requirement, _repository = _traced_runtime()
    runtime.run(requirement=requirement)
    derived = session.run.derived_state
    eval_state, evidence = evaluator._eval_view(derived)

    assert eval_state["status"] == "succeeded"
    assert eval_state["finish_reason"] == "sufficient_evidence"
    assert evidence
    assert eval_state["budget_usage"]["query_calls"] == 2
    assert eval_state["attempted_actions"]

    record = evaluator.CaseRecord(
        case_id="stage10",
        name="阶段 10",
        outcome="pass",
        record={
            "run": {"duration_s": 1.0},
            "runtime": {
                "model_calls": {"value": 4},
                "queries": {"value": 2},
            },
        },
    )
    aggregate = evaluator.build_aggregate([record], elapsed_s=1.0)
    assert aggregate["rates"]["pass_rate"] == 1.0
    assert aggregate["runtime_summary"]["avg_model_calls_observed"] == 4.0
    assert aggregate["runtime_summary"]["avg_queries_observed"] == 2.0
