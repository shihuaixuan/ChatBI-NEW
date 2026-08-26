"""前提 Evidence Gap 的统一校验。

本模块只判断模型提交的 Gap Resolution 是否由当前 Run 的合法 Evidence 支持，
不生成固定查询，也不依赖任何计划节点 ID。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchEvidence,
    ResearchPlanNode,
    SemanticAssessment,
    SemanticAssessmentStatus,
)
from apps.chatbi.services.research.agent_context import evaluate_premise_verdict


def premise_gap_id(requirement: ResearchAgentRequirement) -> str | None:
    """返回冻结 Requirement 中的前提 Gap ID，不假定固定命名。"""

    if requirement.premise_to_verify is None:
        return None
    return next(
        (
            item.requirement_id
            for item in requirement.evidence_requirements
            if item.kind == "premise_confirmation"
        ),
        "premise-gap",
    )


def validate_premise_gap_assessment(
    requirement: ResearchAgentRequirement,
    evidences: Sequence[ResearchEvidence],
    assessment: SemanticAssessment | None,
    *,
    current_result: dict[str, Any] | None,
    plan_nodes: Sequence[ResearchPlanNode] = (),
) -> dict[str, Any] | None:
    """校验前提 Gap 的解决方式，返回需要持久化的裁决结果。"""

    gap_id = premise_gap_id(requirement)
    premise = requirement.premise_to_verify
    if assessment is None:
        if premise is None or gap_id is None or current_result is not None:
            return None
        if not any(
            _plan_node_can_address_premise(requirement, evidences, item)
            for item in plan_nodes
        ):
            raise ValueError("RESEARCH_AGENT_PREMISE_GAP_PLAN_INVALID")
        return None
    if any(item.gap_id != gap_id for item in assessment.resolved_gaps):
        raise ValueError("RESEARCH_AGENT_RESOLVED_GAP_NOT_FOUND")
    if premise is None or gap_id is None or current_result is not None:
        return None

    resolution = next(
        (item for item in assessment.resolved_gaps if item.gap_id == gap_id),
        None,
    )
    unresolved = next(
        (item for item in assessment.unresolved_gaps if item.gap_id == gap_id),
        None,
    )
    if resolution is None:
        if unresolved is None:
            raise ValueError("RESEARCH_AGENT_PREMISE_GAP_MUST_BE_HANDLED")
        if assessment.status is not SemanticAssessmentStatus.EXPLICIT_GAP:
            return None
        relevant_nodes = tuple(
            item for item in plan_nodes if item.gap_id in {None, gap_id}
        )
        if not relevant_nodes:
            raise ValueError("RESEARCH_AGENT_PREMISE_GAP_PLAN_REQUIRED")
        if not any(
            _plan_node_can_address_premise(requirement, evidences, item)
            for item in relevant_nodes
        ):
            raise ValueError("RESEARCH_AGENT_PREMISE_GAP_PLAN_INVALID")
        return None

    if resolution.status == "undetermined":
        raise ValueError("RESEARCH_AGENT_PREMISE_UNDETERMINED_MUST_REMAIN_GAP")
    evidence_by_id = {item.evidence_id: item for item in evidences}
    verdicts: list[
        tuple[Literal["supported", "not_supported"], str, str]
    ] = []
    for evidence_id in resolution.evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None or evidence.run_id != requirement.run_id:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
        verdict, observed = evaluate_premise_verdict(premise, evidence)
        if verdict != "undetermined":
            verdicts.append((verdict, observed, evidence_id))
    if not verdicts:
        raise ValueError("RESEARCH_AGENT_PREMISE_EVIDENCE_INSUFFICIENT")
    distinct_verdicts = {item[0] for item in verdicts}
    if len(distinct_verdicts) != 1:
        raise ValueError("RESEARCH_AGENT_PREMISE_EVIDENCE_CONFLICT")
    verdict, observed, evidence_id = verdicts[0]
    if verdict != resolution.status:
        raise ValueError("RESEARCH_AGENT_PREMISE_RESOLUTION_MISMATCH")
    return {
        "gap_id": gap_id,
        "status": verdict,
        "metric_ref": premise.metric_ref,
        "expected_direction": premise.expected_direction.value,
        "observed_direction": observed,
        "evidence_id": evidence_id,
        "evidence_ids": list(resolution.evidence_ids),
    }


def _plan_node_can_address_premise(
    requirement: ResearchAgentRequirement,
    evidences: Sequence[ResearchEvidence],
    plan_node: ResearchPlanNode,
) -> bool:
    """判断计划步骤是否具备确认前提所需的最小逻辑输入。"""

    premise = requirement.premise_to_verify
    if premise is None:
        return False
    arguments = plan_node.arguments
    if plan_node.tool_name == "query_semantic_data":
        metrics = set(arguments.get("metrics") or ())
        time_roles = set(arguments.get("time_ranges") or ())
        return (
            premise.metric_ref in metrics
            and {item.value for item in premise.time_roles} <= time_roles
            and arguments.get("comparison") == "difference"
        )
    referenced_ids = (
        (arguments.get("evidence_id"),)
        if plan_node.tool_name == "inspect_evidence"
        else tuple(arguments.get("input_evidence_ids") or ())
    )
    evidence_by_id = {item.evidence_id: item for item in evidences}
    return any(
        (evidence := evidence_by_id.get(str(evidence_id))) is not None
        and premise.metric_ref in set(evidence.metric_refs)
        and set(premise.time_roles) <= set(evidence.time_ranges)
        for evidence_id in referenced_ids
    )


__all__ = ["premise_gap_id", "validate_premise_gap_assessment"]
