from __future__ import annotations

from typing import Any

from apps.chatbi.models.dto.question_understanding import (
    QuestionUnderstandingValidationData,
)
from apps.chatbi.services.question_understanding_validation_service import (
    QuestionUnderstandingValidationService,
)


class IntentPostProcessor:
    """意图识别后处理器，统一产出修复、校验和澄清契约。"""

    def __init__(
        self,
        max_retry_count: int = 2,
        validation_service: QuestionUnderstandingValidationService | None = None,
    ) -> None:
        self._max_retry_count = max_retry_count
        self._validation_service = validation_service or QuestionUnderstandingValidationService()

    @property
    def max_retry_count(self) -> int:
        return self._max_retry_count

    def validate(self, intent: dict[str, Any], retry_count: int = 0) -> dict[str, Any]:
        result = self._validation_service.validate(
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
                ambiguous_slots=tuple(
                    str(item) for item in intent.get("ambiguous_slots") or [] if str(item)
                ),
                conflict_slots=tuple(
                    str(item) for item in intent.get("conflict_slots") or [] if str(item)
                ),
                subject_domain=dict(intent.get("subject_domain") or {}),
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
                "max_retry_count": self._max_retry_count,
                "violations": [],
                "clarification_required": bool(slot_issues),
                "slot_issues": slot_issues,
            }

        return {
            "status": "invalid",
            "reason_code": "DIMENSION_VALUE_IS_TIME_EXPRESSION",
            "repair_hint": "普通维度值不能是时间表达；请将时间表达放入 time_range，并将普通维度值标记为未提供。",
            "retryable": next_retry_count < self._max_retry_count,
            "retry_count": next_retry_count,
            "max_retry_count": self._max_retry_count,
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

    def retry_feedback(self, validation: dict[str, Any]) -> dict[str, Any]:
        return {
            "intent_validation": {
                "reason_code": validation.get("reason_code"),
                "repair_hint": validation.get("repair_hint"),
                "violations": validation.get("violations") or [],
                "retry_count": validation.get("retry_count") or 0,
            }
        }

IntentValidationAdapter = IntentPostProcessor
