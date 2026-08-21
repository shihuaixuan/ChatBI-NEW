"""Research 假设状态归并与确定性证据判断。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from apps.chatbi.errors import ResearchExecutionError
from apps.chatbi.models.dto.research import (
    EvidenceSnapshot,
    ResearchDriverRelationship,
    ResearchHypothesis,
    ResearchHypothesisStatus,
    ResearchPolicyDecision,
    ResearchRequirement,
)
from apps.chatbi.services.research.actions import MaterializedResearchAction


def apply_hypothesis_updates(
    current_hypotheses: tuple[ResearchHypothesis, ...],
    decision: ResearchPolicyDecision,
    available_evidence: tuple[EvidenceSnapshot, ...],
) -> tuple[ResearchHypothesis, ...]:
    """合并模型提出的假设变化，并集中校验状态机不变量。"""

    current_by_id = {item.id: item for item in current_hypotheses}
    evidence_ids = {item.evidence_id for item in available_evidence}
    result = dict(current_by_id)
    for item in decision.new_hypotheses:
        if item.id in result:
            raise ResearchExecutionError(ResearchExecutionError.HYPOTHESIS_INVALID)
        if item.status is not ResearchHypothesisStatus.PENDING:
            raise ResearchExecutionError(ResearchExecutionError.HYPOTHESIS_INVALID)
        if not set(item.evidence_ids) <= evidence_ids:
            raise ResearchExecutionError(ResearchExecutionError.HYPOTHESIS_INVALID)
        result[item.id] = item
    for update in decision.hypothesis_updates:
        current = result.get(update.hypothesis_id)
        if current is None:
            raise ResearchExecutionError(ResearchExecutionError.HYPOTHESIS_INVALID)
        if not set(update.evidence_ids) <= evidence_ids:
            raise ResearchExecutionError(
                ResearchExecutionError.HYPOTHESIS_EVIDENCE_REQUIRED
            )
        # invalid 只能由服务端治理校验产生，模型不能自行降低校验标准。
        if update.status is ResearchHypothesisStatus.INVALID:
            raise ResearchExecutionError(ResearchExecutionError.HYPOTHESIS_INVALID)
        if (
            current.status is not ResearchHypothesisStatus.PENDING
            and update.status is ResearchHypothesisStatus.PENDING
        ):
            raise ResearchExecutionError(ResearchExecutionError.HYPOTHESIS_INVALID)
        if (
            update.status
            in {
                ResearchHypothesisStatus.SUPPORTED,
                ResearchHypothesisStatus.WEAKENED,
                ResearchHypothesisStatus.INCONCLUSIVE,
            }
            and not update.evidence_ids
        ):
            raise ResearchExecutionError(
                ResearchExecutionError.HYPOTHESIS_EVIDENCE_REQUIRED
            )
        result[update.hypothesis_id] = current.model_copy(
            update={
                "status": update.status,
                "evidence_ids": update.evidence_ids,
            }
        )
    return tuple(
        result[item.id] for item in current_hypotheses if item.id in result
    ) + tuple(item for item in result.values() if item.id not in current_by_id)


def evaluate_hypothesis_status(
    *,
    metric_refs: tuple[str, ...],
    dimension_refs: tuple[str, ...],
    requirement: ResearchRequirement,
    materialized: MaterializedResearchAction,
    rows: list[dict[str, Any]],
    evidence: EvidenceSnapshot,
) -> ResearchHypothesisStatus:
    """用指标变化方向判断假设，结果只能支持、削弱或无法判断。"""

    relationships = requirement.scope.driver_relationships
    relationship = next(
        (
            item
            for item in relationships
            if set(metric_refs)
            == {
                item.target_metric_ref,
                *(item.component_metric_refs or (item.driver_metric_ref,)),
            }
            and set(dimension_refs) <= set(item.dimension_refs)
            and set(requirement.time_roles) <= set(item.time_roles)
        ),
        None,
    )
    if relationship is None:
        return ResearchHypothesisStatus.INVALID
    if evidence.statistics.truncated:
        return ResearchHypothesisStatus.INCONCLUSIVE
    if relationship.validation_method == "FORMULA_RECONCILIATION":
        return _evaluate_formula_reconciliation(
            relationship=relationship,
            materialized=materialized,
            rows=rows,
        )
    target_field = _difference_field(materialized, relationship.target_metric_ref)
    driver_field = _difference_field(materialized, relationship.driver_metric_ref)
    if target_field is None or driver_field is None:
        return ResearchHypothesisStatus.INCONCLUSIVE
    if relationship.expected_direction == "UNKNOWN":
        return ResearchHypothesisStatus.INCONCLUSIVE
    directions: list[bool] = []
    for row in rows:
        target = _decimal(row.get(target_field))
        driver = _decimal(row.get(driver_field))
        if target is None or driver is None or target == 0 or driver == 0:
            continue
        observed_same_direction = (target > 0) == (driver > 0)
        expected_same_direction = relationship.expected_direction == "POSITIVE"
        directions.append(observed_same_direction == expected_same_direction)
    if not directions:
        return ResearchHypothesisStatus.INCONCLUSIVE
    if all(directions):
        return ResearchHypothesisStatus.SUPPORTED
    if not any(directions):
        return ResearchHypothesisStatus.WEAKENED
    return ResearchHypothesisStatus.INCONCLUSIVE


def _evaluate_formula_reconciliation(
    *,
    relationship: ResearchDriverRelationship,
    materialized: MaterializedResearchAction,
    rows: list[dict[str, Any]],
) -> ResearchHypothesisStatus:
    """仅在所有公式组成指标都出现在结果中时执行公式对账。"""

    formula = relationship.formula_definition
    if not isinstance(formula, dict):
        return ResearchHypothesisStatus.INCONCLUSIVE
    components = formula.get("components")
    operation = str(formula.get("operation") or "").upper()
    if not isinstance(components, list) or operation not in {
        "RATIO",
        "SUM",
        "DIFFERENCE",
        "PRODUCT",
    }:
        return ResearchHypothesisStatus.INCONCLUSIVE
    component_refs = {
        item.get("metric_id")
        for item in components
        if isinstance(item, dict) and isinstance(item.get("metric_id"), int)
    }
    metric_ids_by_ref = {
        ref: int(ref.rsplit(":", 2)[1])
        for ref in materialized.metric_refs
        if isinstance(ref, str) and ref.count(":") == 2 and ref.startswith("METRIC:")
    }
    if not component_refs or not component_refs <= set(metric_ids_by_ref.values()):
        return ResearchHypothesisStatus.INCONCLUSIVE
    target_value_fields = {
        role: _value_field(materialized, relationship.target_metric_ref, role)
        for role in ("value", "current", "previous")
    }
    if all(value is None for value in target_value_fields.values()):
        return ResearchHypothesisStatus.INCONCLUSIVE
    roles = ("value",) if target_value_fields["value"] else ("current", "previous")
    reconciled = 0
    mismatched = 0
    for row in rows:
        for role in roles:
            target_field = target_value_fields[role]
            if target_field is None:
                return ResearchHypothesisStatus.INCONCLUSIVE
            values: dict[str, Decimal] = {}
            missing = False
            for component in components:
                if not isinstance(component, dict):
                    return ResearchHypothesisStatus.INCONCLUSIVE
                metric_id = component.get("metric_id")
                component_ref = next(
                    (
                        ref
                        for ref, current_id in metric_ids_by_ref.items()
                        if current_id == metric_id
                    ),
                    None,
                )
                field = _value_field(materialized, component_ref or "", role)
                value = _decimal(row.get(field)) if field else None
                if value is None:
                    missing = True
                    break
                values[str(component.get("role") or metric_id)] = value
            target = _decimal(row.get(target_field))
            if missing or target is None:
                continue
            try:
                expected = _apply_formula(operation, components, values)
            except (ArithmeticError, KeyError, ValueError):
                continue
            reconciled += 1
            if _close_enough(target, expected):
                continue
            mismatched += 1
    if reconciled == 0:
        return ResearchHypothesisStatus.INCONCLUSIVE
    if mismatched:
        return ResearchHypothesisStatus.WEAKENED
    return ResearchHypothesisStatus.SUPPORTED


def _value_field(
    materialized: MaterializedResearchAction,
    metric_ref: str,
    role: str,
) -> str | None:
    return next(
        (
            item.field
            for item in materialized.columns
            if item.logical_column.metric_ref == metric_ref
            and item.logical_column.value_role == role
        ),
        None,
    )


def _apply_formula(
    operation: str,
    components: list[Any],
    values: dict[str, Decimal],
) -> Decimal:
    ordered = [
        values[str(item.get("role") or item.get("metric_id"))] for item in components
    ]
    if operation == "RATIO":
        if ordered[1] == 0:
            raise ZeroDivisionError
        return ordered[0] / ordered[1]
    if operation == "SUM":
        return sum(ordered, Decimal(0))
    if operation == "DIFFERENCE":
        return ordered[0] - ordered[1]
    if operation == "PRODUCT":
        result = Decimal(1)
        for value in ordered:
            result *= value
        return result
    raise ValueError(operation)


def _close_enough(left: Decimal, right: Decimal) -> bool:
    scale = max(abs(left), abs(right), Decimal(1))
    return abs(left - right) <= Decimal("1e-6") * scale


def apply_deterministic_hypothesis_result(
    *,
    hypotheses: tuple[ResearchHypothesis, ...],
    hypothesis_id: str,
    status: ResearchHypothesisStatus,
    evidence_id: str,
) -> tuple[ResearchHypothesis, ...]:
    """把服务端验证结果追加到假设，避免模型覆盖确定性判断。"""

    target = next((item for item in hypotheses if item.id == hypothesis_id), None)
    if target is None:
        raise ResearchExecutionError(ResearchExecutionError.HYPOTHESIS_INVALID)
    updated = target.model_copy(
        update={
            "status": status,
            "evidence_ids": tuple(dict.fromkeys((*target.evidence_ids, evidence_id))),
        }
    )
    return tuple(updated if item.id == hypothesis_id else item for item in hypotheses)


def _difference_field(
    materialized: MaterializedResearchAction,
    metric_ref: str,
) -> str | None:
    for item in materialized.columns:
        if (
            item.logical_column.metric_ref == metric_ref
            and item.logical_column.value_role == "difference"
        ):
            return item.field
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


__all__ = [
    "apply_deterministic_hypothesis_result",
    "apply_hypothesis_updates",
    "evaluate_hypothesis_status",
]
