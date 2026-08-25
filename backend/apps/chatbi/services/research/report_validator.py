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
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_:])[+-]?\d+(?:\.\d+)?%?")
_IDENTIFIER_PATTERN = re.compile(
    r"(?:evidence:[A-Za-z0-9:_-]+|(?:METRIC|DIMENSION|ASSET):[A-Za-z0-9:_-]+)"
)


def validate_report_conclusions(
    *,
    run_id: str,
    summary: str | None = None,
    summary_evidence_ids: tuple[str, ...] = (),
    summary_limitations: tuple[str, ...] = (),
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
    if summary:
        # summary 会原样进入用户可见报告，必须和结构化结论执行同一硬门禁，
        # 不能成为绕过数字溯源与因果措辞校验的自由文本出口。
        violations.extend(
            _check_conclusion(
                statement=summary,
                evidence_ids=summary_evidence_ids,
                confidence="medium",
                causal=False,
                claim_level=None,
                limitations=summary_limitations,
                run_id=run_id,
                evidence_by_id=evidence_by_id,
                weak_evidence_ids=weak_evidence_ids,
                label="Summary",
            )
        )
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

    # 数字和因果措辞是文本本身的硬门禁；即使没有有效引用，也不能跳过。
    violations.extend(_numeric_violations(label, statement, cited))
    if not causal and _CAUSAL_PATTERN.search(statement):
        violations.append(
            f"{label}：相关性表述不得使用因果措辞；请改为因果声明并引用"
            "对账证据，或改写为相关关系。"
        )
    if cited:
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
    # Evidence ID 和逻辑资产引用只是引用标识，不是报告中的业务数值。
    masked = _IDENTIFIER_PATTERN.sub(lambda item: " " * len(item.group()), statement)
    for match in _NUMBER_PATTERN.finditer(masked):
        raw = statement[match.start() : match.end()]
        # 日期是冻结时间上下文，不是报告自行生成的数据结论。
        if statement[match.end() : match.end() + 1] in {"年", "月", "日"}:
            continue
        value = _canonical(raw.rstrip("%"))
        if value is not None and not _matches_known_number(value, raw, known):
            violations.append(
                f"{label}：数字 {raw} 无法溯源到任何引用证据，禁止无来源数字。"
            )
    return violations


def _matches_known_number(
    value: Decimal,
    raw: str,
    known: set[Decimal],
) -> bool:
    """允许证据值按报告展示精度四舍五入，并接受下降量的绝对值表达。"""

    numeric_raw = raw.rstrip("%")
    decimals = len(numeric_raw.rsplit(".", 1)[1]) if "." in numeric_raw else 0
    tolerance = Decimal("0.5") * (Decimal(10) ** -decimals)
    magnitude = abs(value)
    is_percent = raw.endswith("%")
    return any(
        abs(candidate - value) <= tolerance
        or abs(abs(candidate) - magnitude) <= tolerance
        or (
            is_percent
            and (
                abs(candidate * 100 - value) <= tolerance
                or abs(abs(candidate) * 100 - magnitude) <= tolerance
            )
        )
        for candidate in known
    )


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
    """只比较同一指标在不同证据中的方向，避免多指标驱动分析误判。"""

    directions_by_metric: dict[str, set[str]] = {}
    for item in cited:
        for metric_ref, direction in _metric_directions(item).items():
            directions_by_metric.setdefault(metric_ref, set()).add(direction)
    if any(
        {"increase", "decrease"} <= directions
        for directions in directions_by_metric.values()
    ):
        return "increase vs decrease"
    return None


def _metric_directions(evidence: ResearchEvidence) -> dict[str, str]:
    fields: dict[str, dict[str, str]] = {}
    for column in evidence.logical_columns:
        if column.value_role in ("current", "previous") and column.result_field:
            fields.setdefault(column.asset_ref, {})[column.value_role] = (
                column.result_field
            )
    directions: dict[str, str] = {}
    if not evidence.sample_rows:
        return directions
    row = evidence.sample_rows[0]
    for metric_ref, metric_fields in fields.items():
        if not {"current", "previous"} <= metric_fields.keys():
            continue
        current = _canonical(row.get(metric_fields["current"]))
        previous = _canonical(row.get(metric_fields["previous"]))
        if current is not None and previous is not None:
            if current > previous:
                directions[metric_ref] = "increase"
            elif current < previous:
                directions[metric_ref] = "decrease"
            else:
                directions[metric_ref] = "stable"
    for column in evidence.logical_columns:
        if (
            column.asset_ref in directions
            or column.value_role != "difference"
            or not column.result_field
        ):
            continue
        value = _canonical(row.get(column.result_field))
        if value is not None and value != 0:
            directions[column.asset_ref] = "increase" if value > 0 else "decrease"
        elif value == 0:
            directions[column.asset_ref] = "stable"
    return directions


def _known_numbers(cited: list[ResearchEvidence]) -> set[Decimal]:
    known: set[Decimal] = set()
    for item in cited:
        known.add(Decimal(item.statistics.row_count))
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
