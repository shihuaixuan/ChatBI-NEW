"""问题理解结果的确定性校验规则（Agent 与 Graph 共享，无模型副作用）。"""

from __future__ import annotations

from typing import Any

from apps.chatbi.models.dto.question_understanding import (
    QuestionUnderstandingValidationData,
    QuestionUnderstandingValidationIssue,
    QuestionUnderstandingValidationResult,
)
from apps.temporal import TemporalPlan, is_time_expression


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

    query_shape = data.query_shape
    if query_shape:
        select_mode = str(query_shape.get("select_mode") or "")
        needs_group_by = query_shape.get("needs_group_by") is True
        needs_order_by = query_shape.get("needs_order_by") is True
        order_direction = query_shape.get("order_direction")
        limit = query_shape.get("limit")
        time_grain = query_shape.get("time_grain")
        group_by_slots = [
            slot
            for slot in data.dimension_slots
            if str(slot.get("role") or "").lower() == "group_by"
        ]
        multi_value_filters = [
            slot
            for slot in data.dimension_slots
            if str(slot.get("role") or "").lower() == "filter"
            and isinstance(slot.get("value"), list)
            and len(slot["value"]) >= 2
        ]

        if (data.intent_type == "detail_query") != (select_mode == "detail"):
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="select_mode_conflict",
                    category="intent",
                    clarification_slots=("intent",),
                )
            )
        if needs_order_by and order_direction is None:
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="order_direction_missing",
                    category="intent",
                    clarification_slots=("order",),
                )
            )
        if not needs_order_by and order_direction is not None:
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="order_direction_unexpected",
                    category="repair",
                    clarification_slots=("order",),
                )
            )
        if limit is not None and not needs_order_by:
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="limit_without_order",
                    category="repair",
                    clarification_slots=("limit",),
                )
            )
        if time_grain is not None and not needs_group_by:
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="time_grain_without_grouping",
                    category="repair",
                    clarification_slots=("time_dimension",),
                )
            )
        if needs_group_by and not (
            group_by_slots or multi_value_filters or time_grain is not None
        ):
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="group_by_target_missing",
                    category="intent",
                    clarification_slots=("dimension",),
                )
            )
        if data.intent_type == "ranking_analysis":
            if not group_by_slots:
                issues.append(
                    QuestionUnderstandingValidationIssue(
                        code="ranking_dimension_missing",
                        category="intent",
                        clarification_slots=("dimension",),
                    )
                )
            if not needs_order_by:
                issues.append(
                    QuestionUnderstandingValidationIssue(
                        code="ranking_order_missing",
                        category="intent",
                        clarification_slots=("order",),
                    )
                )
            if limit is None:
                issues.append(
                    QuestionUnderstandingValidationIssue(
                        code="ranking_limit_missing",
                        category="intent",
                        clarification_slots=("limit",),
                    )
                )
        if data.intent_type == "trend_analysis" and not (
            str(data.time_range.get("value_status") or "").lower() == "provided"
            or time_grain is not None
        ):
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="trend_time_missing",
                    category="intent",
                    clarification_slots=("time_range",),
                )
            )

    if data.temporal_plan is not None:
        if data.temporal_plan.status in {"clarification_required", "unsupported"}:
            issues.append(
                QuestionUnderstandingValidationIssue(
                    code="temporal_clarification_required",
                    category="slot",
                    clarification_slots=("time_range",),
                    details={
                        "slot_type": "time_range",
                        "reason": "时间表达需要用户确认后才能继续检索",
                        "plan_status": data.temporal_plan.status,
                        "ambiguity_codes": [
                            ambiguity.code
                            for ambiguity in data.temporal_plan.ambiguities
                        ],
                    },
                )
            )
    elif str(data.time_range.get("value_status") or "").lower() == "provided":
        normalized_time = data.time_range.get("normalized")
        if (
            not isinstance(normalized_time, dict)
            or normalized_time.get("kind") == "unsupported"
        ):
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
                    "candidate_domain_ids": subject_domain.get("candidate_domain_ids")
                    or [],
                },
            )
        )

    for index, slot in enumerate(data.dimension_slots):
        role = str(slot.get("role") or "").lower()
        value_status = str(slot.get("value_status") or "").lower()
        value = slot.get("value")
        dimension = str(slot.get("name") or "维度")
        if (
            role == "filter"
            and value_status == "provided"
            and _dimension_value_is_time_expression(value, data.temporal_plan)
        ):
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


def _dimension_value_is_time_expression(
    value: Any,
    temporal_plan: TemporalPlan | None,
) -> bool:
    """权威模式只依据时间计划识别时间值；旧模式保留固定规则兼容。"""

    if temporal_plan is None:
        return is_time_expression(value)
    raw_expressions = {
        "".join(expression.raw.split()).lower()
        for expression in temporal_plan.expressions
    }
    values = value if isinstance(value, list) else [value]
    return any(
        "".join(str(item or "").split()).lower() in raw_expressions for item in values
    )


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
