"""Research 第三阶段的结构化报告和引用校验。"""

from __future__ import annotations

from typing import Literal

from apps.chatbi.errors import ResearchExecutionError
from apps.chatbi.models.dto.research import (
    EvidenceSnapshot,
    ResearchHypothesisStatus,
    ResearchReport,
    ResearchReportCitation,
    ResearchReportFinding,
    ResearchState,
    ResearchTerminationReason,
)


def compose_research_report(
    *,
    state: ResearchState,
    evidence: tuple[EvidenceSnapshot, ...],
    summary: str,
) -> ResearchReport:
    """只根据运行状态和已成功证据生成报告，不补查数据或新增结论。"""

    evidence_by_id = {item.evidence_id: item for item in evidence}
    citations = tuple(
        ResearchReportCitation(
            evidence_id=item.evidence_id,
            result_id=item.result_id,
            purpose=item.purpose,
        )
        for item in evidence
    )
    findings = tuple(
        _finding(item)
        for item in evidence
        if item.evidence_id in evidence_by_id
    )
    limitations = [
        limitation
        for item in evidence
        for limitation in item.limitations
    ]
    if any(item.statistics.truncated for item in evidence):
        limitations.append("部分证据摘要已按预算截断，未覆盖完整结果集。")
    if state.finish_reason is ResearchTerminationReason.BUDGET_EXHAUSTED:
        limitations.append("研究达到预算上限，部分方向尚未完成验证。")
    failed_action_count = sum(
        len(item.failed_actions) for item in state.iteration_records
    )
    if failed_action_count:
        limitations.append(
            f"共有 {failed_action_count} 个研究动作执行失败，相关方向未形成证据。"
        )
    report = ResearchReport(
        goal=state.goal,
        summary=summary or "研究已完成现有证据范围内的分析。",
        termination_reason=state.finish_reason
        or _fallback_termination_reason(state),
        findings=findings,
        supported_hypotheses=tuple(
            item
            for item in state.hypotheses
            if item.status is ResearchHypothesisStatus.SUPPORTED
        ),
        weakened_hypotheses=tuple(
            item
            for item in state.hypotheses
            if item.status is ResearchHypothesisStatus.WEAKENED
        ),
        inconclusive_hypotheses=tuple(
            item
            for item in state.hypotheses
            if item.status is ResearchHypothesisStatus.INCONCLUSIVE
        ),
        unverified_hypotheses=tuple(
            item
            for item in state.hypotheses
            if item.status in {
                ResearchHypothesisStatus.PENDING,
                ResearchHypothesisStatus.INVALID,
            }
        ),
        limitations=tuple(dict.fromkeys(limitations)),
        citations=citations,
    )
    _validate_report_evidence(report, evidence_by_id)
    return report


def _finding(evidence: EvidenceSnapshot) -> ResearchReportFinding:
    roles = {item.value_role for item in evidence.logical_columns}
    if "contribution" in roles:
        claim_level: Literal[
            "contribution", "common_change", "correlation_clue", "limitation"
        ] = "contribution"
        statement = f"{evidence.purpose}，贡献度结果已生成并通过结果校验。"
        confidence: Literal["high", "medium", "low"] = "high"
    elif "difference" in roles or "growth_rate" in roles:
        claim_level = "common_change"
        statement = f"{evidence.purpose}，结果包含 {evidence.statistics.row_count} 行变化证据。"
        confidence = "medium"
    else:
        claim_level = "correlation_clue"
        statement = f"{evidence.purpose}，结果包含 {evidence.statistics.row_count} 行可继续验证的证据。"
        confidence = "low"
    return ResearchReportFinding(
        statement=statement,
        evidence_ids=(evidence.evidence_id,),
        confidence=confidence,
        claim_level=claim_level,
    )


def _validate_report_evidence(
    report: ResearchReport,
    evidence_by_id: dict[str, EvidenceSnapshot],
) -> None:
    """报告中的每一条发现都必须引用当前 Run 已有证据。"""

    if any(
        evidence_id not in evidence_by_id
        for finding in report.findings
        for evidence_id in finding.evidence_ids
    ):
        raise ResearchExecutionError(ResearchExecutionError.REPORT_INVALID)


def _fallback_termination_reason(state: ResearchState) -> ResearchTerminationReason:
    # 该分支只用于构造未结束状态的内部报告，正式收口必须有终止原因。
    if state.status.value == "failed":
        return ResearchTerminationReason.EXECUTION_FAILED
    return ResearchTerminationReason.DATA_INSUFFICIENT


__all__ = ["compose_research_report"]
