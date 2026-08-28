"""41/42 目标契约的阶段 1测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.chatbi.models.dto.research_agent import (
    AttemptSummary,
    BudgetUsage,
    CompareMetricsOperation,
    Completion,
    CompletionLimitation,
    ComputeEvidenceAction,
    ComputeEvidenceArguments,
    ConversationMessage,
    Evidence,
    EvidenceColumn,
    EvidenceComputation,
    EvidenceData,
    EvidenceDefinition,
    ExecutionError,
    FinalFindingDraft,
    Finding,
    FindingChange,
    FindingScope,
    FinishResearchAction,
    FinishResearchArguments,
    MetricFormula,
    QueryOrder,
    QuerySemanticDataAction,
    QuerySemanticDataArguments,
    ResearchActionType,
    ResearchAgentInput,
    ResearchExecutionErrorStage,
    ResearchState,
    ResearchStateSnapshot,
    ResearchStateStatus,
    ResearchTurnDecision,
    SemanticAmbiguity,
    SemanticContext,
    SemanticDimension,
    SemanticHierarchy,
    SemanticMetric,
    TodoChange,
    TodoItem,
    ToolResult,
    ToolResultStatus,
)


def _semantic_context() -> SemanticContext:
    return SemanticContext(
        metrics=(
            SemanticMetric(
                ref="METRIC:12:gmv",
                name="GMV",
                description="支付成功商品的成交金额",
                aggregation="SUM",
                unit="元",
                dimensions=("DIMENSION:12:pay_date",),
            ),
        ),
        dimensions=(
            SemanticDimension(
                ref="DIMENSION:12:pay_date",
                name="支付日期",
                description="支付成功日期",
            ),
        ),
        hierarchies=(
            SemanticHierarchy(
                ref="HIERARCHY:12:merchant_booth",
                name="商家档口层级",
                levels=("DIMENSION:12:merchant", "DIMENSION:12:booth"),
            ),
        ),
    )


def _query_action() -> QuerySemanticDataAction:
    return QuerySemanticDataAction(
        purpose="查询两期 GMV",
        expected_result="得到当前期和对比期 GMV",
        arguments=QuerySemanticDataArguments(
            request=CompareMetricsOperation(
                metric_refs=("METRIC:12:gmv",),
                order_by=(
                    QueryOrder(
                        field_ref="METRIC:12:gmv",
                        value_role="difference",
                        direction="asc",
                    ),
                ),
                limit=20,
            ),
        ),
    )


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="evidence:tool_call_01",
        evidence_type="query_result",
        purpose="对比两天 GMV",
        definition=EvidenceDefinition(
            metrics=("METRIC:12:gmv",),
            dimensions=("DIMENSION:12:pay_date",),
        ),
        columns=(
            EvidenceColumn(
                name="pay_date",
                semantic_ref="DIMENSION:12:pay_date",
                role="dimension",
                data_type="date",
            ),
            EvidenceColumn(
                name="gmv",
                semantic_ref="METRIC:12:gmv",
                role="metric",
                data_type="decimal",
                unit="元",
            ),
        ),
        data=EvidenceData(
            row_count=2,
            rows=(("2026-06-28", 100000), ("2026-06-29", 80000)),
        ),
    )


def _finding() -> Finding:
    return Finding(
        finding_id="finding_01",
        statement="6月29日 GMV 比 6月28日低。",
        evidence_ids=("evidence:tool_call_01",),
        scope=FindingScope(
            metric_refs=("METRIC:12:gmv",),
            dimension_refs=("DIMENSION:12:pay_date",),
        ),
    )


def test_research_agent_input_accepts_architecture_shape() -> None:
    value = ResearchAgentInput(
        agent_input_ref="agent_input_01",
        user_question="分析 GMV 变化",
        conversation_context=(ConversationMessage(role="user", content="请分析"),),
        semantic_context=_semantic_context(),
    )

    assert value.schema_version == 1
    assert value.semantic_context.metrics[0].ref == "METRIC:12:gmv"


def test_semantic_context_rejects_duplicate_asset_refs() -> None:
    with pytest.raises(ValidationError, match="RESEARCH_AGENT_SEMANTIC_CONTEXT_METRICS_DUPLICATED"):
        SemanticContext(
            metrics=(
                SemanticMetric(ref="METRIC:12:gmv", name="GMV", aggregation="SUM"),
                SemanticMetric(ref="METRIC:12:gmv", name="GMV", aggregation="SUM"),
            ),
        )


def test_semantic_context_accepts_formula_and_ambiguity() -> None:
    value = SemanticContext(
        metrics=(
            SemanticMetric(ref="METRIC:12:gmv", name="GMV", aggregation="SUM"),
            SemanticMetric(ref="METRIC:12:orders", name="订单数", aggregation="SUM"),
        ),
        metric_formulas=(
            MetricFormula(
                target_metric_ref="METRIC:12:aov",
                expression="METRIC:12:gmv / METRIC:12:orders",
                source_metric_refs=("METRIC:12:gmv", "METRIC:12:orders"),
            ),
        ),
        ambiguities=(
            SemanticAmbiguity(
                term="销售额",
                candidate_refs=("METRIC:12:gmv", "METRIC:12:orders"),
            ),
        ),
    )

    assert value.metric_formulas[0].target_metric_ref == "METRIC:12:aov"


def test_unknown_schema_version_is_rejected() -> None:
    with pytest.raises(ValidationError, match="RESEARCH_AGENT_SCHEMA_VERSION_UNSUPPORTED"):
        ResearchAgentInput(
            schema_version=99,
            user_question="查询 GMV",
            semantic_context=SemanticContext(),
        )


def test_evidence_id_is_deterministically_derived_from_tool_call_id() -> None:
    assert Evidence.evidence_id_for_tool_call("tool_call_01") == "evidence:tool_call_01"


def test_evidence_rejects_invalid_row_width_and_computation_without_parent() -> None:
    with pytest.raises(ValidationError, match="RESEARCH_AGENT_EVIDENCE_ROW_WIDTH_INVALID"):
        Evidence(
            evidence_id="evidence:tool_call_01",
            evidence_type="query_result",
            purpose="查询结果",
            definition=EvidenceDefinition(metrics=("METRIC:12:gmv",)),
            columns=(
                EvidenceColumn(
                    name="gmv",
                    semantic_ref="METRIC:12:gmv",
                    role="metric",
                    data_type="decimal",
                ),
            ),
            data=EvidenceData(row_count=1, rows=((1, 2),)),
        )

    with pytest.raises(ValidationError, match="RESEARCH_AGENT_COMPUTATION_PARENT_EVIDENCE_REQUIRED"):
        Evidence(
            evidence_id="evidence:tool_call_02",
            evidence_type="computation_result",
            purpose="计算结果",
            definition=EvidenceDefinition(
                computation=EvidenceComputation(
                    operation="difference",
                    input_evidence_ids=("evidence:tool_call_01",),
                ),
            ),
            columns=(
                EvidenceColumn(
                    name="difference",
                    role="computed",
                    data_type="decimal",
                ),
            ),
            data=EvidenceData(row_count=0),
        )


def test_tool_result_requires_result_or_error_by_status() -> None:
    evidence = _evidence()
    success = ToolResult[Evidence](
        tool_call_id="tool_call_01",
        name=ResearchActionType.QUERY_SEMANTIC_DATA,
        status=ToolResultStatus.SUCCEEDED,
        result=evidence,
    )
    assert success.result == evidence

    with pytest.raises(ValidationError, match="RESEARCH_AGENT_TOOL_RESULT_FAILURE_CONTRACT_INVALID"):
        ToolResult[Evidence](
            tool_call_id="tool_call_02",
            name=ResearchActionType.QUERY_SEMANTIC_DATA,
            status=ToolResultStatus.FAILED,
            result=evidence,
        )

    with pytest.raises(ValidationError, match="RESEARCH_AGENT_WAITING_STATUS_TOOL_INVALID"):
        ToolResult[Evidence](
            tool_call_id="tool_call_03",
            name=ResearchActionType.QUERY_SEMANTIC_DATA,
            status=ToolResultStatus.WAITING_FOR_USER,
            result=evidence,
        )


def test_execution_error_retry_flags_are_consistent() -> None:
    with pytest.raises(ValidationError, match="RESEARCH_AGENT_PARAMETER_RETRY_REQUIRES_RETRYABLE"):
        ExecutionError(
            code="INVALID_QUERY",
            stage=ResearchExecutionErrorStage.VALIDATION,
            message="参数无效",
            parameter_retryable=True,
        )


def test_research_turn_decision_parses_discriminated_action() -> None:
    decision = ResearchTurnDecision.model_validate(
        {
            "actions": [
                {
                    "action_type": "query_semantic_data",
                    "purpose": "查询 GMV",
                    "arguments": {
                        "request": {
                            "operation": "metric_snapshot",
                            "metric_refs": ["METRIC:12:gmv"],
                            "limit": 20,
                        },
                    },
                },
            ],
        }
    )

    assert isinstance(decision.actions[0], QuerySemanticDataAction)


def test_finish_action_must_be_alone() -> None:
    completion = Completion(
        status="complete",
        summary="已有证据足以回答",
        evidence_ids=("evidence:tool_call_01",),
    )
    finish = FinishResearchAction(
        purpose="提交完成结果",
        arguments=FinishResearchArguments(
            completion=completion,
            findings=(
                FinalFindingDraft(
                    statement="已有证据足以回答",
                    evidence_ids=("evidence:tool_call_01",),
                ),
            ),
        ),
    )

    with pytest.raises(ValidationError, match="RESEARCH_AGENT_CONTROL_ACTION_MUST_BE_ALONE"):
        ResearchTurnDecision(actions=(_query_action(), finish))


def test_parallel_actions_only_allow_read_only_actions() -> None:
    compute = ComputeEvidenceAction(
        purpose="计算差值",
        arguments=ComputeEvidenceArguments(
            operation="difference",
            input_evidence_ids=("evidence:tool_call_01",),
        ),
    )
    with pytest.raises(
        ValidationError,
        match="RESEARCH_AGENT_PARALLEL_ACTION_NOT_READ_ONLY",
    ):
        ResearchTurnDecision(actions=(_query_action(), compute))

    with pytest.raises(ValidationError, match="RESEARCH_AGENT_CONTROL_ACTION_MUST_BE_ALONE"):
        ResearchTurnDecision(
            actions=(
                _query_action(),
                FinishResearchAction(
                    purpose="提交完成结果",
                        arguments=FinishResearchArguments(
                            completion=Completion(
                            status="complete",
                            summary="已有证据足以回答",
                                evidence_ids=("evidence:tool_call_01",),
                            ),
                            findings=(
                                FinalFindingDraft(
                                    statement="已有证据足以回答",
                                    evidence_ids=("evidence:tool_call_01",),
                                ),
                            ),
                        ),
                ),
            ),
        )


def test_finding_change_requires_payload_matching_change_type() -> None:
    finding = _finding()
    assert FindingChange(change_type="add", finding=finding, reason="新证据支持")

    with pytest.raises(ValidationError, match="RESEARCH_AGENT_FINDING_ADD_PAYLOAD_INVALID"):
        FindingChange(
            change_type="add",
            finding=finding,
            finding_id="finding_old",
            reason="不允许同时提交旧 ID",
        )

    with pytest.raises(ValidationError, match="RESEARCH_AGENT_FINDING_SUPERSEDE_PAYLOAD_INVALID"):
        FindingChange(change_type="supersede", reason="缺少被替代 Finding")


def test_todo_change_requires_fields_matching_change_type() -> None:
    todo = TodoItem(
        todo_id="todo_01",
        goal="定位下降最大的商家",
        status="in_progress",
        order=1,
        related_evidence_ids=("evidence:tool_call_01",),
    )
    assert TodoChange(change_type="add", todo=todo)
    assert TodoChange(
        change_type="set_status",
        todo_id="todo_01",
        status="completed",
        result_reason="已完成商家分析",
    )

    with pytest.raises(ValidationError, match="RESEARCH_AGENT_TODO_ORDER_PAYLOAD_INVALID"):
        TodoChange(change_type="set_order", todo_id="todo_01")


def test_state_rejects_terminal_status_without_completion() -> None:
    with pytest.raises(ValidationError, match="RESEARCH_AGENT_STATE_COMPLETION_REQUIRED"):
        ResearchState(
            agent_input_ref="agent_input_01",
            current_status=ResearchStateStatus.COMPLETED,
        )


def test_state_requires_completion_status_to_match_run_status() -> None:
    with pytest.raises(ValidationError, match="RESEARCH_AGENT_STATE_COMPLETION_STATUS_INVALID"):
        ResearchState(
            agent_input_ref="agent_input_01",
            current_status=ResearchStateStatus.PARTIAL,
            completion=Completion(
                status="complete",
                summary="完成",
                evidence_ids=("evidence:tool_call_01",),
            ),
        )


def test_partial_and_unanswerable_completion_require_limitations() -> None:
    limitation = CompletionLimitation(
        code="DATA_MISSING",
        description="没有可用数据",
        impact="无法支持结论",
        attempt_ids=("attempt_01",),
    )
    completion = Completion(
        status="unanswerable",
        summary="当前无法回答",
        limitations=(limitation,),
    )
    assert completion.status == "unanswerable"


def test_research_state_snapshot_requires_matching_input_snapshot() -> None:
    state = ResearchState(agent_input_ref="agent_input_01")
    snapshot = ResearchStateSnapshot(
        state=state,
        last_event_sequence=0,
        input_snapshot_ref="agent_input_01",
    )
    assert snapshot.runtime_type == "research_agent"

    with pytest.raises(ValidationError, match="RESEARCH_AGENT_SNAPSHOT_INPUT_REF_MISMATCH"):
        ResearchStateSnapshot(
            state=state,
            last_event_sequence=0,
            input_snapshot_ref="agent_input_02",
        )


def test_physical_sql_payload_is_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        QuerySemanticDataArguments.model_validate(
            {
                "metrics": ["METRIC:12:gmv"],
                "result": {"limit": 20},
                "sql": "SELECT 1",
            }
        )


def test_attempt_summary_requires_error_for_rejected_action() -> None:
    with pytest.raises(ValidationError, match="RESEARCH_AGENT_ATTEMPT_ERROR_REQUIRED"):
        AttemptSummary(
            attempt_id="attempt_01",
            action_type=ResearchActionType.QUERY_SEMANTIC_DATA,
            purpose="查询数据",
            action_fingerprint="query:fingerprint",
            status="rejected",
        )


def test_budget_usage_rejects_negative_values() -> None:
    with pytest.raises(ValidationError):
        BudgetUsage(query_calls=-1)
