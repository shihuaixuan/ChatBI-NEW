"""问题理解结果的确定性校验规则（Agent 与 Graph 共享，无模型副作用）。"""

from __future__ import annotations

from typing import Any

from apps.chatbi.models.dto.question_understanding import (
    QuestionUnderstandingValidationData,
    QuestionUnderstandingValidationIssue,
    QuestionUnderstandingValidationResult,
)
from apps.chatbi.services.understanding.time_range import is_time_expression


def validate_question_understanding(
    data: QuestionUnderstandingValidationData,
) -> QuestionUnderstandingValidationResult:
    """统一校验 Agent 与 Graph 已识别的问题理解结果。"""

    issues: list[QuestionUnderstandingValidationIssue] = []

    if data.rewrite_need_user_input:
        issues.append(
            QuestionUnderstandingValidationIssue(
                code="rewrite_context_incomplete",
                category="rewrite",
                clarification_slots=data.rewrite_missing_slots,
            )
        )
    if data.intent_type == "unknown":
        issues.append(
            QuestionUnderstandingValidationIssue(
                code="intent_unknown",
                category="intent",
                clarification_slots=("intent",),
            )
        )
    if data.intent_type != "detail_query" and not data.metric_mentions:
        issues.append(
            QuestionUnderstandingValidationIssue(
                code="metric_missing",
                category="intent",
                clarification_slots=("metric",),
            )
        )
    if data.conflict_slots:
        issues.append(
            QuestionUnderstandingValidationIssue(
                code="intent_conflict",
                category="intent",
                clarification_slots=data.conflict_slots,
            )
        )

    if str(data.time_range.get("value_status") or "").lower() == "provided":
        normalized_time = data.time_range.get("normalized")
        if not isinstance(normalized_time, dict) or normalized_time.get("kind") == "unsupported":
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="time_range_unsupported",
                    category="slot",
                    clarification_slots=("time_range",),
                )
            )

    ambiguous_dimension_names = {
        str(slot.get("name") or "")
        for slot in data.dimension_slots
        if str(slot.get("role") or "").lower() == "ambiguous"
    }
    if data.ambiguous_slots:
        issues.append(
            QuestionUnderstandingValidationIssue(
                code="intent_ambiguous",
                category="intent",
                clarification_slots=tuple(
                    slot
                    for slot in data.ambiguous_slots
                    if slot not in ambiguous_dimension_names
                ),
            )
        )

    subject_domain = data.subject_domain
    subject_status = str(subject_domain.get("status") or "").lower()
    if subject_status in {"ambiguous", "not_matched"}:
        issues.append(
            QuestionUnderstandingValidationIssue(
                code="subject_domain_ambiguous",
                category="slot",
                clarification_slots=("subject_domain",),
                details={
                    "slot_type": "subject_domain",
                    "reason": str(subject_domain.get("reason") or "主题域未能唯一确定"),
                    "candidate_domain_ids": subject_domain.get("candidate_domain_ids") or [],
                },
            )
        )

    for index, slot in enumerate(data.dimension_slots):
        role = str(slot.get("role") or "").lower()
        value_status = str(slot.get("value_status") or "").lower()
        value = slot.get("value")
        dimension = str(slot.get("name") or "维度")
        if role == "filter" and value_status == "provided" and is_time_expression(value):
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="dimension_value_is_time_expression",
                    category="repair",
                    clarification_slots=("filter_value",),
                    details={
                        "slot": f"dimension_slots[{index}].value",
                        "dimension": dimension,
                        "value": value,
                    },
                )
            )
        if role == "ambiguous":
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="dimension_role_ambiguous",
                    category="slot",
                    clarification_slots=("dimension",),
                    details=_dimension_issue_details(dimension, role, value_status),
                )
            )
        if role == "filter" and value_status == "ambiguous":
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="dimension_value_ambiguous",
                    category="slot",
                    clarification_slots=("filter_value",),
                    details=_dimension_issue_details(dimension, role, value_status),
                )
            )
        if role == "filter" and (
            value_status != "provided"
            or value is None
            or (isinstance(value, str) and not value.strip())
        ):
            # 筛选维度没有值时不可执行，不能让错误意图继续污染语义资产检索。
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="dimension_filter_value_missing",
                    category="slot",
                    clarification_slots=("filter_value",),
                    details=_dimension_issue_details(dimension, role, value_status),
                )
            )

    return QuestionUnderstandingValidationResult(issues=tuple(issues))


def _dimension_issue_details(
    dimension: str,
    role: str,
    value_status: str,
) -> dict[str, Any]:
    return {
        "slot_type": "dimension_value",
        "dimension": dimension,
        "role": role or "ambiguous",
        "value_status": value_status or "not_provided",
        "reason": f"用户提到了{dimension}维度，但没有提供具体值或分组方式",
    }


__all__ = ["validate_question_understanding"]
