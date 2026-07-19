"""自然语言意图子任务结果的确定性投影服务。"""

from __future__ import annotations

from typing import Any

from apps.chatbi.models.dto.question_understanding import (
    QuestionIntentProjectionData,
    QuestionIntentProjectionResult,
)
from apps.chatbi.services.time_range import normalize_time_range_payload

_INTENT_TYPES = {
    "metric_query",
    "trend_analysis",
    "ranking_analysis",
    "comparison_analysis",
    "detail_query",
    "share_analysis",
    "anomaly_analysis",
    "unknown",
}
_REQUIRED_SLOT_TYPES = {
    "metric",
    "dimension",
    "time_dimension",
    "time_range",
    "filter",
    "order",
    "limit",
    "comparison_target",
}
_INTENT_FEEDBACK_SLOTS = {
    "intent",
    "intent_type",
    "analysis_type",
    "analysis_mode",
    "query_shape",
}


class QuestionIntentProjectionService:
    """统一清洗、合并和修正自然语言意图，不负责模型调用与降级。"""

    def normalize_shape(
        self,
        payload: dict[str, Any],
        *,
        subject_domain: dict[str, Any],
    ) -> dict[str, Any]:
        """清洗分析形态子任务输出，主题域由 Graph 候选规则预先确定。"""

        return {
            "intent_type": self.normalize_intent_type(payload.get("intent_type")),
            "confidence": self.normalize_confidence(payload.get("confidence")),
            "required_slot_types": self.normalize_required_slot_types(
                payload.get("required_slot_types")
            ),
            "query_shape": (
                payload.get("query_shape")
                if isinstance(payload.get("query_shape"), dict)
                else {}
            ),
            "subject_domain": dict(subject_domain),
            "ambiguous_slots": self.normalize_text_list(
                payload.get("ambiguous_slots")
            ),
            "conflict_slots": self.normalize_text_list(payload.get("conflict_slots")),
        }

    def normalize_semantic(self, payload: dict[str, Any]) -> dict[str, Any]:
        """清洗指标和时间子任务输出，并统一生成时间 AST。"""

        time_mentions = self.normalize_text_list(payload.get("time_mentions"))
        time_range = payload.get("time_range") or self.time_range_from_mentions(
            time_mentions
        )
        if not isinstance(time_range, dict):
            raise ValueError("QUESTION_INTENT_TIME_RANGE_INVALID")
        return {
            "metric_mentions": self.normalize_text_list(payload.get("metric_mentions")),
            "time_mentions": time_mentions,
            "time_range": normalize_time_range_payload(time_range),
            "ambiguous_slots": self.normalize_text_list(
                payload.get("ambiguous_slots")
            ),
            "conflict_slots": self.normalize_text_list(payload.get("conflict_slots")),
        }

    def project(
        self,
        data: QuestionIntentProjectionData,
    ) -> QuestionIntentProjectionResult:
        """合并三类子任务结果，并集中表达必需槽位和歧义规则。"""

        shape = data.shape
        semantic = data.semantic
        dimensions = data.dimensions
        ambiguous_slots = self.unique_strings(
            [
                *self.normalize_text_list(shape.get("ambiguous_slots")),
                *self.normalize_text_list(semantic.get("ambiguous_slots")),
                *self.normalize_text_list(dimensions.get("ambiguous_slots")),
            ]
        )
        conflict_slots = self.unique_strings(
            [
                *self.normalize_text_list(shape.get("conflict_slots")),
                *self.normalize_text_list(semantic.get("conflict_slots")),
                *self.normalize_text_list(dimensions.get("conflict_slots")),
            ]
        )
        metric_mentions = self.normalize_text_list(semantic.get("metric_mentions"))
        if not metric_mentions and "metric" not in ambiguous_slots:
            ambiguous_slots.append("metric")

        raw_dimension_slots = dimensions.get("dimension_slots")
        dimension_slots: list[Any] = (
            raw_dimension_slots if isinstance(raw_dimension_slots, list) else []
        )
        for slot in dimension_slots:
            if (
                isinstance(slot, dict)
                and str(slot.get("value_status") or "").lower() == "ambiguous"
                and "filter_value" not in ambiguous_slots
            ):
                ambiguous_slots.append("filter_value")

        required_slot_types = self.normalize_required_slot_types(
            shape.get("required_slot_types")
        )
        raw_time_range = semantic.get("time_range")
        time_range: dict[str, Any] = (
            raw_time_range if isinstance(raw_time_range, dict) else {}
        )
        if (
            str(time_range.get("value_status") or "").lower() == "provided"
            and "time_dimension" not in required_slot_types
        ):
            required_slot_types.append("time_dimension")

        payload = {
            "intent_type": self.normalize_intent_type(shape.get("intent_type")),
            "confidence": min(
                self.normalize_confidence(shape.get("confidence")),
                self.normalize_confidence(semantic.get("confidence", 1.0)),
                self.normalize_confidence(dimensions.get("confidence", 1.0)),
            ),
            "metric_mentions": metric_mentions,
            "dimension_mentions": self.unique_strings(
                [
                    *self.normalize_text_list(dimensions.get("dimension_mentions")),
                    *[
                        str(slot.get("name"))
                        for slot in dimension_slots
                        if isinstance(slot, dict) and slot.get("name")
                    ],
                ]
            ),
            "dimension_slots": dimension_slots,
            "time_mentions": self.normalize_text_list(semantic.get("time_mentions")),
            "time_range": time_range
            or {"raw": None, "value_status": "not_provided"},
            "filter_mentions": (
                dimensions.get("residual_filter_mentions")
                if isinstance(dimensions.get("residual_filter_mentions"), list)
                else []
            ),
            "required_slot_types": required_slot_types,
            "query_shape": (
                shape.get("query_shape")
                if isinstance(shape.get("query_shape"), dict)
                else {}
            ),
            "subject_domain": (
                shape.get("subject_domain")
                if isinstance(shape.get("subject_domain"), dict)
                else _default_subject_domain()
            ),
            "ambiguous_slots": ambiguous_slots,
            "conflict_slots": conflict_slots,
        }
        return QuestionIntentProjectionResult(
            payload=self._apply_confirmed_intent(payload, data.user_feedback)
        )

    @staticmethod
    def normalize_intent_type(value: Any) -> str:
        intent_type = str(value or "").strip()
        return intent_type if intent_type in _INTENT_TYPES else "unknown"

    def normalize_required_slot_types(self, value: Any) -> list[str]:
        return [
            item
            for item in self.normalize_text_list(value)
            if item in _REQUIRED_SLOT_TYPES
        ]

    @staticmethod
    def normalize_dimension_role(value: Any) -> str:
        role = str(value or "").strip().lower()
        return role if role in {"group_by", "filter", "ambiguous"} else "ambiguous"

    @staticmethod
    def normalize_value_status(status: Any, value: Any) -> str:
        normalized = str(status or "").strip().lower()
        if normalized in {"provided", "not_provided", "ambiguous"}:
            return normalized
        return "provided" if value not in (None, "") else "not_provided"

    @staticmethod
    def normalize_confidence(value: Any) -> float:
        try:
            return min(max(float(value), 0.0), 1.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def unique_strings(values: list[Any]) -> list[str]:
        result: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    @staticmethod
    def normalize_text_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item or "").strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def time_range_from_mentions(time_mentions: list[str]) -> dict[str, Any]:
        if not time_mentions:
            return {"raw": None, "value_status": "not_provided"}
        return {"raw": time_mentions[0], "value_status": "provided"}

    def _apply_confirmed_intent(
        self,
        payload: dict[str, Any],
        user_feedback: dict[str, Any],
    ) -> dict[str, Any]:
        confirmed_intent = _confirmed_intent_type(user_feedback)
        if not confirmed_intent:
            return payload
        return {
            **payload,
            "intent_type": confirmed_intent,
            "confidence": max(self.normalize_confidence(payload.get("confidence")), 0.95),
            "ambiguous_slots": _without_intent_slots(payload.get("ambiguous_slots")),
            "conflict_slots": _without_intent_slots(payload.get("conflict_slots")),
        }


def _confirmed_intent_type(user_feedback: dict[str, Any]) -> str | None:
    if user_feedback.get("skipped") is True:
        return None
    value = (
        user_feedback.get("intent")
        or user_feedback.get("intent_type")
        or user_feedback.get("analysis_type")
    )
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _without_intent_slots(value: Any) -> list[str]:
    return [
        slot
        for slot in QuestionIntentProjectionService.normalize_text_list(value)
        if slot not in _INTENT_FEEDBACK_SLOTS
    ]


def _default_subject_domain() -> dict[str, Any]:
    return {
        "status": "not_required",
        "domain_id": None,
        "domain_name": None,
        "domain_biz_name": None,
        "confidence": 0.0,
        "reason": "",
        "candidate_domain_ids": [],
    }


__all__ = ["QuestionIntentProjectionService"]
