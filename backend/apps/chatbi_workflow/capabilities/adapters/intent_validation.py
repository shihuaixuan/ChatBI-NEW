from __future__ import annotations

from typing import Any

from apps.chatbi_workflow.capabilities.adapters.time_slots import is_time_expression


class IntentPostProcessor:
    """意图识别后处理器，统一产出修复、校验和澄清契约。"""

    def __init__(self, max_retry_count: int = 2) -> None:
        self._max_retry_count = max_retry_count

    @property
    def max_retry_count(self) -> int:
        return self._max_retry_count

    def validate(self, intent: dict[str, Any], retry_count: int = 0) -> dict[str, Any]:
        violations = self._dimension_time_value_violations(intent)
        next_retry_count = retry_count + 1 if violations else retry_count
        if not violations:
            slot_issues = self._slot_issues(intent)
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

    @staticmethod
    def _slot_issues(intent: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        subject_domain = intent.get("subject_domain") if isinstance(intent.get("subject_domain"), dict) else {}
        if str(subject_domain.get("status") or "").lower() in {"ambiguous", "not_matched"}:
            issues.append(
                {
                    "slot_type": "subject_domain",
                    "reason": str(subject_domain.get("reason") or "主题域未能唯一确定"),
                    "candidate_domain_ids": subject_domain.get("candidate_domain_ids") or [],
                }
            )

        dimension_slots = intent.get("dimension_slots")
        if isinstance(dimension_slots, list):
            for slot in dimension_slots:
                if not isinstance(slot, dict):
                    continue
                role = str(slot.get("role") or "").lower()
                value_status = str(slot.get("value_status") or "").lower()
                if role in {"ambiguous", "filter"} and value_status != "provided":
                    dimension = str(slot.get("name") or "维度")
                    issues.append(
                        {
                            "slot_type": "dimension_value",
                            "dimension": dimension,
                            "role": role or "ambiguous",
                            "value_status": value_status or "not_provided",
                            "reason": f"用户提到了{dimension}维度，但没有提供具体值或分组方式",
                        }
                    )
        return issues

    @staticmethod
    def _dimension_time_value_violations(intent: dict[str, Any]) -> list[dict[str, Any]]:
        dimension_slots = intent.get("dimension_slots")
        if not isinstance(dimension_slots, list):
            return []
        violations: list[dict[str, Any]] = []
        for index, slot in enumerate(dimension_slots):
            if not isinstance(slot, dict):
                continue
            if str(slot.get("role") or "").lower() != "filter":
                continue
            if str(slot.get("value_status") or "").lower() != "provided":
                continue
            value = slot.get("value")
            if is_time_expression(value):
                violations.append(
                    {
                        "slot": f"dimension_slots[{index}].value",
                        "dimension": slot.get("name"),
                        "value": value,
                    }
                )
        return violations

IntentValidationAdapter = IntentPostProcessor
