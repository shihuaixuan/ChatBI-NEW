"""服务端报告校验（doc38 §10.3.4）。

对 finish_research 提交的 findings / claims 做 100% 硬门禁：

1. 证据存在且属于当前 Run；
2. Finding 中的数字必须出现在引用证据的样本行里；
3. INCONCLUSIVE 假设不能支撑高置信结论；
4. 贡献度/因果结论必须通过对账或贡献度计算；
5. 数据截断时禁止“全部/唯一”等绝对表述；
6. 相关性结论不得使用因果措辞；
7. EXPLORATORY 证据必须在 limitations 中披露。

校验返回全部违规项；任何违规都让 finish 整体失败——不允许删掉引用后
继续输出原结论。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from apps.chatbi.models.dto.research_agent import (
    ResearchClaim,
    ResearchClaimLevel,
    ResearchEvidence,
    ResearchEvidenceLevel,
    ResearchHypothesisAssessment,
    ResearchReportFinding,
)

_ABSOLUTE_PATTERN = re.compile(r"全部|所有|唯一|必然|绝对|一定")
_CAUSAL_PATTERN = re.compile(r"导致|造成|使得|归因于|根因|根本原因|驱动了")
_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")


def validate_report_conclusions(
    *,
    run_id: str,
    findings: tuple[ResearchReportFinding, ...] = (),
    claims: tuple[ResearchClaim, ...] = (),
    evidences: list[ResearchEvidence] | tuple[ResearchEvidence, ...] = (),
    assessments: tuple[ResearchHypothesisAssessment, ...] = (),
) -> tuple[str, ...]:
    """返回全部违规描述；空元组表示通过。"""

    evidence_by_id = {item.evidence_id: item for item in evidences}
    weak_evidence_ids = {
        evidence_id
        for item in assessments
        if item.assessment == "inconclusive"
        for evidence_id in item.evidence_ids
    }
    violations: list[str] = []
    for finding in findings:
        violations.extend(
            _check_conclusion(
                statement=finding.statement,
                evidence_ids=finding.evidence_ids,
                confidence=finding.confidence,
                causal=bool(finding.statement_kind == "causal"),
                claim_level=None,
                limitations=finding.limitations,
                run_id=run_id,
                evidence_by_id=evidence_by_id,
                weak_evidence_ids=weak_evidence_ids,
                label=f"Finding「{finding.statement[:50]}」",
            )
        )
    for claim in claims:
        violations.extend(
            _check_conclusion(
                statement=claim.statement,
                evidence_ids=claim.evidence_ids,
                confidence=claim.confidence,
                causal=False,
                claim_level=claim.claim_level,
                limitations=(),
                run_id=run_id,
                evidence_by_id=evidence_by_id,
                weak_evidence_ids=weak_evidence_ids,
                label=f"Claim「{claim.statement[:50]}」",
            )
        )
    return tuple(violations)


def _check_conclusion(
    *,
    statement: str,
    evidence_ids: tuple[str, ...],
    confidence: str,
    causal: bool,
    claim_level: ResearchClaimLevel | None,
    limitations: tuple[str, ...],
    run_id: str,
    evidence_by_id: dict[str, ResearchEvidence],
    weak_evidence_ids: set[str],
    label: str,
) -> list[str]:
    cited: list[ResearchEvidence] = []
    violations: list[str] = []
    for evidence_id in evidence_ids:
        item = evidence_by_id.get(evidence_id)
        if item is None:
            violations.append(f"{label}：引用的证据 {evidence_id} 不存在。")
            continue
        if item.run_id != run_id:
            violations.append(f"{label}：引用的证据 {evidence_id} 不属于当前 Run。")
            continue
        cited.append(item)

    if cited:
        violations.extend(_numeric_violations(label, statement, cited))
        violations.extend(
            _truncation_violations(label, statement, cited)
        )
        violations.extend(
            _exploratory_violations(label, cited, limitations)
        )
        if confidence == "high" and weak_evidence_ids & {
            item.evidence_id for item in cited
        }:
            violations.append(
                f"{label}：引用了被判定为无法判断的证据，不能给出高置信结论。"
            )
        needs_reconciliation = causal or (
            claim_level is not None and claim_level is ResearchClaimLevel.CONTRIBUTION
        )
        if needs_reconciliation and not any(_has_reconciliation(item) for item in cited):
            violations.append(
                f"{label}：贡献度/因果结论必须引用经过对账或贡献度计算的"
                "证据，相关性观察不足以支撑。"
            )
        if not causal and _CAUSAL_PATTERN.search(statement):
            violations.append(
                f"{label}：相关性表述不得使用因果措辞；请改为因果声明并引用"
                "对账证据，或改写为相关关系。"
            )
        conflicts = _direction_conflicts(cited)
        if conflicts and (confidence == "high" or causal):
            violations.append(
                f"{label}：引用的证据方向相互矛盾（{conflicts}），"
                "只能给出低置信的相关性表述。"
            )
    return violations


def _numeric_violations(
    label: str,
    statement: str,
    cited: list[ResearchEvidence],
) -> list[str]:
    known = _known_numbers(cited)
    violations: list[str] = []
    for raw in _NUMBER_PATTERN.findall(statement):
        value = _canonical(raw)
        if value is not None and value not in known:
            violations.append(
                f"{label}：数字 {raw} 无法溯源到任何引用证据，禁止无来源数字。"
            )
    return violations


def _truncation_violations(
    label: str,
    statement: str,
    cited: list[ResearchEvidence],
) -> list[str]:
    if not any(item.statistics.truncated for item in cited):
        return []
    matched = _ABSOLUTE_PATTERN.search(statement)
    if matched is None:
        return []
    return [
        f"{label}：引用了截断数据，禁止使用绝对表述（命中：{matched.group()}）。"
    ]


def _exploratory_violations(
    label: str,
    cited: list[ResearchEvidence],
    limitations: tuple[str, ...],
) -> list[str]:
    if not any(
        item.evidence_level is ResearchEvidenceLevel.EXPLORATORY for item in cited
    ):
        return []
    if limitations:
        return []
    return [
        f"{label}：引用了 EXPLORATORY 证据，必须在 limitations 中披露其探索属性。"
    ]


def _has_reconciliation(evidence: ResearchEvidence) -> bool:
    return any(
        dependency.relation in {"reconciliation", "contribution"}
        for dependency in evidence.dependencies
    )


def _direction_conflicts(cited: list[ResearchEvidence]) -> str | None:
    """检测引用证据之间可判定的方向矛盾；不可判定返回 None。"""

    directions: set[str] = set()
    for item in cited:
        direction = _observed_direction(item)
        if direction is not None:
            directions.add(direction)
    if {"increase", "decrease"} <= directions:
        return "increase vs decrease"
    return None


def _observed_direction(evidence: ResearchEvidence) -> str | None:
    fields: dict[str, str] = {}
    for column in evidence.logical_columns:
        if column.value_role in ("current", "previous") and column.result_field:
            fields[column.value_role] = column.result_field
    if {"current", "previous"} <= fields.keys() and evidence.sample_rows:
        row = evidence.sample_rows[0]
        current = _canonical(row.get(fields["current"]))
        previous = _canonical(row.get(fields["previous"]))
        if current is not None and previous is not None:
            if current > previous:
                return "increase"
            if current < previous:
                return "decrease"
            return "stable"
    difference = next(
        (
            column.result_field
            for column in evidence.logical_columns
            if column.value_role == "difference" and column.result_field
        ),
        None,
    )
    if difference and evidence.sample_rows:
        value = _canonical(evidence.sample_rows[0].get(difference))
        if value is not None and value != 0:
            return "increase" if value > 0 else "decrease"
    return None


def _known_numbers(cited: list[ResearchEvidence]) -> set[Decimal]:
    known: set[Decimal] = set()
    for item in cited:
        for row in item.sample_rows:
            for value in row.values():
                number = _canonical(value)
                if number is not None:
                    known.add(number)
    return known


def _canonical(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number.normalize()


__all__ = ["validate_report_conclusions"]
