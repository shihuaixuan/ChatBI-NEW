from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from typing import Any

from apps.agentic_chat.services.query_understanding_models import (
    QueryUnderstandingConfig,
    QueryUnderstandingContext,
    QueryUnderstandingResult,
    SlotCandidate,
    SlotIssue,
)
from apps.agentic_chat.services.query_understanding_prompt import (
    parse_model_json,
    validate_asset_bindings,
)
from apps.agentic_chat.services.query_understanding_rules import QueryUnderstandingRules
from common.core.config import settings


class QueryUnderstandingService:
    def __init__(self, config: QueryUnderstandingConfig | None = None, model_enabled: bool | None = None, model_client=None):
        self.config = config or QueryUnderstandingConfig(
            model_enabled=settings.QUERY_UNDERSTANDING_MODEL_ENABLED,
            timeout_ms=settings.QUERY_UNDERSTANDING_TIMEOUT_MS,
            min_confidence=settings.QUERY_UNDERSTANDING_MIN_CONFIDENCE,
            core_slot_min_confidence=settings.CORE_SLOT_MIN_CONFIDENCE,
            metric_accept_score=settings.METRIC_ACCEPT_SCORE,
            metric_ambiguity_gap=settings.METRIC_AMBIGUITY_GAP,
            exact_alias_accept=settings.EXACT_ALIAS_ACCEPT,
            clarification_max_options=settings.CLARIFICATION_MAX_OPTIONS,
        )
        if model_enabled is not None:
            self.config.model_enabled = model_enabled
        self.model_client = model_client
        self.rules = QueryUnderstandingRules(self.config)

    def understand(self, context: QueryUnderstandingContext) -> QueryUnderstandingResult:
        fallback = self.rules.understand_by_rules(context)
        model_payload = self.call_model(context)
        if model_payload:
            # 模型有有效输出时，以模型理解为主；规则结果只作为字段补齐和模型失败时的兜底。
            result = self._result_from_model_payload(model_payload, fallback=fallback)
        else:
            result = fallback
        result = self.merge_confirmed_slots(result, context.confirmed_slots)
        result.retrieval_queries = self.build_retrieval_queries(result, context.question)
        return result

    def call_model(self, context: QueryUnderstandingContext) -> dict[str, Any]:
        if not self.config.model_enabled or self.model_client is None:
            return {}
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.model_client, context)
                raw = future.result(timeout=self.config.timeout_ms / 1000)
        except (Exception, TimeoutError):
            return {}
        payload = parse_model_json(raw if isinstance(raw, str) else str(raw))
        if not payload:
            return {}
        return self.sanitize_model_payload(payload, context)

    def merge_confirmed_slots(self, result: QueryUnderstandingResult, confirmed_slots: dict[str, Any]) -> QueryUnderstandingResult:
        if not confirmed_slots:
            return result
        updated = result.model_copy(deep=True)
        for slot_name, value in confirmed_slots.items():
            canonical = self._canonical_slot_name(slot_name)
            confirmed_value = self._confirmed_slot_value(canonical, value)
            existing_values = updated.slots.get(canonical) or []
            if existing_values and not self._same_slot(existing_values[0], confirmed_value):
                updated.conflict_slots.append(
                    SlotIssue(
                        slot=canonical,
                        raw_text=str(value),
                        reason="本轮理解结果与用户已确认槽位冲突，保留用户确认槽位。",
                        sources=["confirmed_slots", "query_understanding"],
                    )
                )
            updated.slots[canonical] = [confirmed_value]
            updated.missing_slots = [item for item in updated.missing_slots if item != canonical and item != slot_name]
            updated.low_confidence_slots = [issue for issue in updated.low_confidence_slots if issue.slot != canonical]
            updated.ambiguous_slots = [issue for issue in updated.ambiguous_slots if issue.slot != canonical]
        return updated

    @staticmethod
    def build_retrieval_queries(result: QueryUnderstandingResult, original_question: str) -> list[str]:
        queries: list[str] = []
        for query in (result.normalized_question, original_question):
            if query and query not in queries:
                queries.append(query)
        metrics = result.slots.get("metrics") or []
        time_ranges = result.slots.get("time_range") or []
        metric_name = metrics[0].get("display_name") if metrics else None
        time_name = time_ranges[0].get("display_name") if time_ranges else None
        if metric_name and time_name:
            queries.append(f"{time_name}{metric_name}")
        if metric_name and result.intent == "trend_analysis":
            queries.append(f"{metric_name} 按日期 趋势")
        return list(dict.fromkeys([query for query in queries if query]))[:5]

    @staticmethod
    def sanitize_model_payload(payload: dict[str, Any], context: QueryUnderstandingContext) -> dict[str, Any]:
        allowed = {
            (str(candidate.asset_type), int(candidate.asset_id))
            for candidate in context.semantic_candidates
            if candidate.asset_type and candidate.asset_id is not None
        }
        return validate_asset_bindings(payload, allowed)

    @staticmethod
    def _result_from_model_payload(payload: dict[str, Any], fallback: QueryUnderstandingResult) -> QueryUnderstandingResult:
        data = {
            "normalized_question": payload.get("normalized_question") or fallback.normalized_question,
            "intent": payload.get("intent") or fallback.intent,
            "intent_confidence": payload.get("intent_confidence") or fallback.intent_confidence,
            "slots": payload.get("slots") or {},
            "missing_slots": payload.get("missing_slots") or [],
            "low_confidence_slots": payload.get("low_confidence_slots") or [],
            "ambiguous_slots": payload.get("ambiguous_slots") or [],
            "conflict_slots": payload.get("conflict_slots") or [],
            "retrieval_queries": payload.get("retrieval_queries") or [],
            "confidence": payload.get("confidence") or fallback.confidence,
            "can_answer_with_assumption": payload.get("can_answer_with_assumption") or False,
        }
        try:
            return QueryUnderstandingResult(**data)
        except Exception:
            return fallback

    @staticmethod
    def _merge_rule_and_model_results(
        rule_result: QueryUnderstandingResult,
        model_result: QueryUnderstandingResult,
    ) -> QueryUnderstandingResult:
        merged = rule_result.model_copy(deep=True)
        if model_result.intent != "unknown":
            merged.intent = model_result.intent
            merged.intent_confidence = max(merged.intent_confidence, model_result.intent_confidence)
        for slot_name, slot_values in model_result.slots.items():
            if slot_values and slot_name not in merged.slots:
                merged.slots[slot_name] = slot_values
            elif slot_values and slot_name == "metrics" and merged.slots.get("metrics"):
                rule_metric = merged.slots["metrics"][0]
                model_metric = slot_values[0]
                if not QueryUnderstandingService._same_slot(rule_metric, model_metric):
                    merged.conflict_slots.append(
                        SlotIssue(
                            slot="metrics",
                            raw_text=model_metric.get("raw_text") or model_metric.get("display_name"),
                            reason="模型抽取指标与规则/语义候选命中的指标不一致，保留规则可判定结果。",
                            candidates=[
                                QueryUnderstandingService._dict_to_candidate(rule_metric),
                                QueryUnderstandingService._dict_to_candidate(model_metric),
                            ],
                            sources=["rule", "llm"],
                        )
                    )
        merged.missing_slots = list(dict.fromkeys([*merged.missing_slots, *model_result.missing_slots]))
        merged.low_confidence_slots.extend(model_result.low_confidence_slots)
        merged.ambiguous_slots.extend(model_result.ambiguous_slots)
        merged.conflict_slots.extend(model_result.conflict_slots)
        merged.confidence = max(merged.confidence, model_result.confidence)
        return merged

    @staticmethod
    def _canonical_slot_name(slot_name: str) -> str:
        return {"metric": "metrics", "dimension": "dimensions"}.get(slot_name, slot_name)

    @staticmethod
    def _confirmed_slot_value(slot_name: str, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            data = deepcopy(value)
        else:
            data = {"name": str(value), "display_name": str(value), "raw_text": str(value), "value": value}
        data.setdefault("name", data.get("display_name") or str(value))
        data.setdefault("display_name", data.get("name") or str(value))
        data.setdefault("raw_text", data.get("display_name"))
        data.setdefault("confidence", 1.0)
        data.setdefault("source", "confirmed")
        data["confirmed"] = True
        return data

    @staticmethod
    def _same_slot(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return (left.get("asset_id") and left.get("asset_id") == right.get("asset_id")) or left.get("display_name") == right.get("display_name")

    @staticmethod
    def _dict_to_candidate(value: dict[str, Any]) -> SlotCandidate:
        return SlotCandidate(
            display_name=value.get("display_name") or value.get("name") or "",
            raw_text=value.get("raw_text"),
            asset_type=value.get("asset_type"),
            asset_id=value.get("asset_id"),
            score=float(value.get("confidence") or value.get("score") or 0),
            source=value.get("source") or "unknown",
        )
