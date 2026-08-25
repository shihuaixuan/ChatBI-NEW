"""Evidence Requirement 最低结构覆盖评估（doc38 §10.3.2）。

只根据冻结 Requirement、当前 Run 的证据台账和前提确认结果推导结构化
覆盖与缺口；不读取 Evidence 实际数据内容，不判断能否回答用户问题，
也不决定下一个工具。
"""

from __future__ import annotations

from typing import Any, TypeAlias

from apps.chatbi.models.dto.analysis_evidence import AnalysisEvidence
from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchEvidence,
    StructuralCoverage,
    StructuralCoverageGap,
)

EvidenceItem: TypeAlias = AnalysisEvidence | ResearchEvidence


def evaluate_structural_coverage(
    requirement: ResearchAgentRequirement,
    evidences: list[EvidenceItem] | tuple[EvidenceItem, ...],
    *,
    premise_result: dict[str, Any] | None = None,
) -> StructuralCoverage:
    """按字段、数量和依赖关系判断最低结构覆盖，不检查实际数据语义。"""

    expected_version = requirement.version_snapshot.model_dump(mode="json")
    invalid_evidence_refs = tuple(
        item.evidence_id
        for item in evidences
        if (
            isinstance(item, ResearchEvidence)
            and item.run_id != requirement.run_id
        )
        or item.version_snapshot.model_dump(mode="json") != expected_version
    )
    invalid_ids = set(invalid_evidence_refs)
    evidence_items = [
        item for item in evidences if item.evidence_id not in invalid_ids
    ]
    target_refs = set(requirement.target_metric_refs)
    coverage: dict[str, int] = dict.fromkeys(requirement.target_metric_refs, 0)
    for item in evidence_items:
        for ref in target_refs & set(item.metric_refs):
            coverage[ref] += 1

    premise_handled = (
        requirement.premise_to_verify is None or premise_result is not None
    )
    gaps: list[StructuralCoverageGap] = []
    covered_requirements: list[str] = []
    if not premise_handled:
        gaps.append(
            StructuralCoverageGap(
                requirement_id=None,
                kind="premise",
                missing_count=1,
                message="前提尚未确认，不能提交充分结论。",
            )
        )

    for req in requirement.evidence_requirements:
        if req.kind == "premise_confirmation":
            # 前提类需求由 premise_handled 统一判断，不按证据数量计。
            if premise_handled:
                covered_requirements.append(req.requirement_id)
            continue
        covered = _covered_count(req.kind, req.required_asset_refs, evidence_items)
        if covered < req.minimum_count:
            gaps.append(
                StructuralCoverageGap(
                    requirement_id=req.requirement_id,
                    kind=req.kind,
                    missing_count=req.minimum_count - covered,
                    message=(
                        f"证据需求 {req.requirement_id}（{req.kind}）"
                        f"还缺 {req.minimum_count - covered} 条：{req.description}"
                    ),
                )
            )
        else:
            covered_requirements.append(req.requirement_id)

    core_supported = any(
        _is_governed(item)
        and target_refs & set(item.metric_refs)
        for item in evidence_items
    )
    return StructuralCoverage(
        minimum_requirements_met=not gaps and not invalid_evidence_refs,
        premise_handled=premise_handled,
        core_supported=core_supported,
        covered_requirements=tuple(covered_requirements),
        missing_requirements=tuple(gaps),
        invalid_evidence_refs=invalid_evidence_refs,
        target_metric_coverage=coverage,
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
    "evaluate_structural_coverage",
]
