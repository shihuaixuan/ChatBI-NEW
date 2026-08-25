"""Evidence Requirement 最低结构覆盖评估（doc38 §10.3.2）。

只根据冻结 Requirement、当前 Run 的证据台账和前提确认结果推导结构化
覆盖与缺口；不读取 Evidence 实际数据内容，不判断能否回答用户问题，
也不决定下一个工具。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

from apps.chatbi.models.dto.analysis_evidence import AnalysisEvidence
from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchEvidence,
)

EvidenceItem: TypeAlias = AnalysisEvidence | ResearchEvidence


@dataclass(frozen=True)
class ResearchCompletionGap:
    """一条尚未满足的证据需求。"""

    requirement_id: str | None
    kind: str
    missing_count: int
    message: str


@dataclass(frozen=True)
class ResearchCompletionEvaluation:
    """最低结构覆盖结果，不代表 Evidence 内容已经充分。"""

    minimum_requirements_met: bool
    premise_handled: bool
    core_supported: bool
    target_metric_coverage: dict[str, int]
    gaps: tuple[ResearchCompletionGap, ...]

    def gap_messages(self) -> tuple[str, ...]:
        return tuple(item.message for item in self.gaps)


def evaluate_completion(
    requirement: ResearchAgentRequirement,
    evidences: list[EvidenceItem] | tuple[EvidenceItem, ...],
    *,
    premise_result: dict[str, Any] | None = None,
) -> ResearchCompletionEvaluation:
    """按字段、数量和依赖关系判断最低结构覆盖，不检查实际数据语义。"""

    evidence_items = list(evidences)
    target_refs = set(requirement.target_metric_refs)
    coverage: dict[str, int] = dict.fromkeys(requirement.target_metric_refs, 0)
    for item in evidence_items:
        for ref in target_refs & set(item.metric_refs):
            coverage[ref] += 1

    premise_handled = (
        requirement.premise_to_verify is None or premise_result is not None
    )
    gaps: list[ResearchCompletionGap] = []
    if not premise_handled:
        gaps.append(
            ResearchCompletionGap(
                requirement_id=None,
                kind="premise",
                missing_count=1,
                message="前提尚未确认，不能提交充分结论。",
            )
        )

    for req in requirement.evidence_requirements:
        if req.kind == "premise_confirmation":
            # 前提类需求由 premise_handled 统一判断，不按证据数量计。
            continue
        covered = _covered_count(req.kind, req.required_asset_refs, evidence_items)
        if covered < req.minimum_count:
            gaps.append(
                ResearchCompletionGap(
                    requirement_id=req.requirement_id,
                    kind=req.kind,
                    missing_count=req.minimum_count - covered,
                    message=(
                        f"证据需求 {req.requirement_id}（{req.kind}）"
                        f"还缺 {req.minimum_count - covered} 条：{req.description}"
                    ),
                )
            )

    core_supported = any(
        _is_governed(item)
        and target_refs & set(item.metric_refs)
        for item in evidence_items
    )
    return ResearchCompletionEvaluation(
        minimum_requirements_met=not gaps,
        premise_handled=premise_handled,
        core_supported=core_supported,
        target_metric_coverage=coverage,
        gaps=tuple(gaps),
    )


def _covered_count(
    kind: str,
    required_asset_refs: tuple[str, ...],
    evidences: list[EvidenceItem],
) -> int:
    """不同需求类型的覆盖口径；只看证据内容，不看工具调用记录。"""

    required = set(required_asset_refs)
    if kind == "premise_confirmation":
        # 前提类需求由 premise_result 判断，证据数量不计入。
        return 0
    if kind == "claim_support":
        return sum(
            1
            for item in evidences
            if _is_governed(item)
            and (not required or required & set(item.metric_refs))
        )
    if kind == "counter_evidence":
        # 反证：不触及必需资产、来自其他角度的证据。
        return sum(
            1
            for item in evidences
            if not required or not required & set(item.metric_refs)
        )
    if kind == "reconciliation":
        return sum(
            1
            for item in evidences
            if (
                not required
                or required <= (set(item.metric_refs) | set(item.dimension_refs))
            )
            if any(
                dependency.relation in {"reconciliation", "contribution"}
                for dependency in item.dependencies
            )
        )
    # dimension_or_driver_analysis 及其他类型：资产交集计数。
    return sum(
        1
        for item in evidences
        if not required
        or required
        & (set(item.metric_refs) | set(item.dimension_refs))
    )


def _is_governed(evidence: EvidenceItem) -> bool:
    """兼容旧 Research 枚举和统一 Evidence 枚举。"""

    level = evidence.evidence_level
    return getattr(level, "value", level) == "governed"


__all__ = [
    "ResearchCompletionEvaluation",
    "ResearchCompletionGap",
    "evaluate_completion",
]
