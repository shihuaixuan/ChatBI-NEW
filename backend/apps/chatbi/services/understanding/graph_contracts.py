"""Graph 专用的问题输入与意图契约投影（纯函数 + 兼容薄包装）。

本模块只做"共享理解结果 → Graph 节点稳定契约"的确定性映射；模型调用、重试编排、
降级触发仍由 Graph 编排层负责。
"""

from __future__ import annotations

from typing import Any

from apps.chatbi.models.dto.question_understanding import (
    QuestionClassificationOutputBase,
    QuestionRewriteProjectionOutput,
    QuestionUnderstandingValidationData,
)
from apps.chatbi.services.understanding.validation import (
    validate_question_understanding,
)
from apps.temporal import TemporalPlan

DEFAULT_MAX_INTENT_RETRY = 2


# --- 问题分类与重写投影（原 QuestionInputProjectionService） ---


def classification_precondition(
    question: str,
    dataset_id: int | None,
) -> dict[str, Any] | None:
    """返回无需调用模型的分类结果；可继续分类时返回空值。"""

    if not question:
        return QuestionClassificationOutputBase(
            category="forbidden",
            reason="empty_question",
            risk_level="medium",
            confidence=1.0,
        ).model_dump(mode="json")
    if dataset_id is None:
        return QuestionClassificationOutputBase(
            category="forbidden",
            reason="missing_dataset",
            risk_level="medium",
            confidence=1.0,
        ).model_dump(mode="json")
    return None


def project_classification(payload: dict[str, Any]) -> dict[str, Any]:
    """校验并输出稳定的问题分类结构。"""

    return QuestionClassificationOutputBase.model_validate(payload).model_dump(
        mode="json"
    )


def project_rewrite(
    payload: dict[str, Any],
    dataset_id: int | None,
) -> dict[str, Any]:
    """校验重写输出，并清除模型误报的已提供数据集槽位。"""

    output = QuestionRewriteProjectionOutput.model_validate(payload)
    if dataset_id is None or "dataset_id" not in output.missing_slots:
        return output.model_dump(mode="json")
    missing_slots = [slot for slot in output.missing_slots if slot != "dataset_id"]
    return output.model_copy(
        update={
            "missing_slots": missing_slots,
            "need_user_input": bool(missing_slots),
        }
    ).model_dump(mode="json")


def empty_rewrite() -> dict[str, Any]:
    """投影空问题的固定澄清结果。"""

    return QuestionRewriteProjectionOutput(
        rewritten_question="",
        need_user_input=True,
        missing_slots=["question"],
        image_profile_hint=None,
    ).model_dump(mode="json")


def fallback_rewrite(
    question: str,
    user_feedback: dict[str, Any],
) -> dict[str, Any]:
    """模型失败时生成 Graph 既有的最小重写降级结果。"""

    need_user_input = not user_feedback and any(
        keyword in question for keyword in ("需要澄清", "信息不足", "补充")
    )
    return QuestionRewriteProjectionOutput(
        rewritten_question=question,
        need_user_input=need_user_input,
        missing_slots=["metric"] if need_user_input else [],
        image_profile_hint=None,
    ).model_dump(mode="json")


# --- 意图校验投影 ---


def validate_intent(
    intent: dict[str, Any],
    retry_count: int = 0,
    *,
    max_retry_count: int = DEFAULT_MAX_INTENT_RETRY,
) -> dict[str, Any]:
    """将统一问题理解校验结果投影为 Graph 稳定意图校验契约。"""

    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            intent_type=str(intent.get("intent_type") or "unknown"),
            metric_mentions=tuple(
                str(item) for item in intent.get("metric_mentions") or [] if str(item)
            ),
            dimension_slots=tuple(
                dict(slot)
                for slot in intent.get("dimension_slots") or []
                if isinstance(slot, dict)
            ),
            time_range=dict(intent.get("time_range") or {}),
            time_ranges=tuple(
                dict(item)
                for item in intent.get("time_ranges") or []
                if isinstance(item, dict)
            ),
            comparison=dict(intent.get("comparison") or {}),
            composition=dict(intent.get("composition") or {}),
            multi_step=dict(intent.get("multi_step") or {}),
            query_shape=dict(intent.get("query_shape") or {}),
            ambiguous_slots=tuple(
                str(item) for item in intent.get("ambiguous_slots") or [] if str(item)
            ),
            conflict_slots=tuple(
                str(item) for item in intent.get("conflict_slots") or [] if str(item)
            ),
            subject_domain=dict(intent.get("subject_domain") or {}),
            temporal_plan=(
                TemporalPlan.model_validate(intent["temporal_plan"])
                if isinstance(intent.get("temporal_plan"), dict)
                else None
            ),
        )
    )
    repair_issues = [issue for issue in result.issues if issue.category == "repair"]
    violations = [issue.details for issue in repair_issues]
    next_retry_count = retry_count + 1 if violations else retry_count
    if not violations:
        graph_slot_codes = {
            "subject_domain_ambiguous",
            "dimension_role_ambiguous",
            "dimension_filter_value_missing",
            "temporal_clarification_required",
        }
        slot_issues = [
            issue.details
            for issue in result.issues
            if issue.code in graph_slot_codes and issue.details
        ]
        return {
            "status": "valid",
            "reason_code": "INTENT_VALID",
            "repair_hint": None,
            "retryable": False,
            "retry_count": retry_count,
            "max_retry_count": max_retry_count,
            "violations": [],
            "clarification_required": bool(slot_issues),
            "slot_issues": slot_issues,
        }

    return {
        "status": "invalid",
        "reason_code": "DIMENSION_VALUE_IS_TIME_EXPRESSION",
        "repair_hint": "普通维度值不能是时间表达；请将时间表达放入 time_range，并将普通维度值标记为未提供。",
        "retryable": next_retry_count < max_retry_count,
        "retry_count": next_retry_count,
        "max_retry_count": max_retry_count,
        "violations": violations,
        "clarification_required": True,
        "slot_issues": [
            {
                "slot_type": "dimension_value",
                "dimension": str(violation.get("dimension") or "维度"),
                "role": "ambiguous",
                "value_status": "not_provided",
                "reason": "维度值被识别成时间表达，需要用户确认维度值或分组方式",
            }
            for violation in violations
        ],
    }


def intent_retry_feedback(validation: dict[str, Any]) -> dict[str, Any]:
    """构造稳定的模型修复反馈，供编排层决定是否再次调用模型。"""

    return {
        "intent_validation": {
            "reason_code": validation.get("reason_code"),
            "repair_hint": validation.get("repair_hint"),
            "violations": validation.get("violations") or [],
            "retry_count": validation.get("retry_count") or 0,
        }
    }


__all__ = [
    "DEFAULT_MAX_INTENT_RETRY",
    "classification_precondition",
    "empty_rewrite",
    "fallback_rewrite",
    "intent_retry_feedback",
    "project_classification",
    "project_rewrite",
    "validate_intent",
]
