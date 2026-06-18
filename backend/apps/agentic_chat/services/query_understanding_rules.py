from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from apps.agentic_chat.services.query_understanding_models import (
    QueryUnderstandingConfig,
    QueryUnderstandingContext,
    QueryUnderstandingResult,
    SlotCandidate,
    SlotIssue,
)

GENERIC_METRIC_WORDS = ("额度", "金额", "数量", "业绩", "收入", "成本")
NATURAL_METRIC_WORDS = ("销售额", "订单数", "销售量", "成交额", "客单价", "收入", "成本", "利润")
TIME_FIELD_HINTS = ("时间", "日期", "date", "time", "_at")
ENTITY_HINTS = ("华东", "华南", "华北", "华中", "东北", "西南", "西北")


class QueryUnderstandingRules:
    def __init__(self, config: QueryUnderstandingConfig | None = None):
        self.config = config or QueryUnderstandingConfig()

    def understand_by_rules(self, context: QueryUnderstandingContext) -> QueryUnderstandingResult:
        intent = self._infer_intent(context.question)
        slots: dict[str, list[dict[str, Any]]] = {}
        missing_slots: list[str] = []
        low_confidence_slots: list[SlotIssue] = []
        ambiguous_slots: list[SlotIssue] = []
        conflict_slots: list[SlotIssue] = []

        time_slot = self._extract_time_range(context.question, context.current_time)
        if time_slot:
            slots["time_range"] = [time_slot]

        top_n = self._extract_top_n(context.question, intent)
        if top_n:
            slots["top_n"] = [top_n]

        ordering = self._extract_ordering(context.question)
        if ordering:
            slots["ordering"] = [ordering]

        metric_result = self._resolve_metric(context)
        if metric_result["accepted"]:
            slots["metrics"] = [metric_result["accepted"]]
        low_confidence_slots.extend(metric_result["low_confidence"])
        ambiguous_slots.extend(metric_result["ambiguous"])
        conflict_slots.extend(metric_result["conflict"])

        dimension_result = self._resolve_dimension(context)
        if dimension_result["accepted"]:
            slots["dimensions"] = [dimension_result["accepted"]]
        low_confidence_slots.extend(dimension_result["low_confidence"])
        ambiguous_slots.extend(dimension_result["ambiguous"])
        conflict_slots.extend(dimension_result["conflict"])
        if not dimension_result["accepted"] and not dimension_result["ambiguous"]:
            entity_issue = self._detect_unmapped_entity(context)
            if entity_issue:
                low_confidence_slots.append(entity_issue)

        ambiguous_time = self._detect_ambiguous_time_field(context)
        if ambiguous_time and "time_range" not in slots:
            ambiguous_slots.append(ambiguous_time)

        if not context.datasource_id:
            missing_slots.append("datasource")

        for slot in self._required_slots(intent):
            if slot not in slots and not self._has_issue(slot, low_confidence_slots, ambiguous_slots, conflict_slots):
                missing_slots.append(slot)

        normalized_question = self._normalize_question(context.question, slots)
        return QueryUnderstandingResult(
            normalized_question=normalized_question,
            intent=intent,
            intent_confidence=0.82 if intent != "unknown" else 0.4,
            slots=slots,
            missing_slots=self._dedupe(missing_slots),
            low_confidence_slots=low_confidence_slots,
            ambiguous_slots=ambiguous_slots,
            conflict_slots=conflict_slots,
            confidence=0.78 if not (missing_slots or low_confidence_slots or ambiguous_slots or conflict_slots) else 0.62,
        )

    def _resolve_metric(self, context: QueryUnderstandingContext) -> dict[str, Any]:
        candidates = [candidate for candidate in context.semantic_candidates if self._is_metric_candidate(candidate)]
        candidates = sorted(candidates, key=lambda item: item.score, reverse=True)
        if not candidates:
            natural_metric = self._extract_natural_metric(context.question)
            return {"accepted": natural_metric, "low_confidence": [], "ambiguous": [], "conflict": []}

        exact = self._find_exact_metric(context.question, candidates)
        if exact and self.config.exact_alias_accept:
            return {"accepted": self._candidate_to_slot(exact, context.question, 0.92), "low_confidence": [], "ambiguous": [], "conflict": []}

        top = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        raw_text = self._matched_generic_word(context.question)
        if raw_text and len(candidates) > 1:
            close_candidates = [item for item in candidates if raw_text in item.display_name or item.raw_text == raw_text]
            if len(close_candidates) > 1:
                return {
                    "accepted": None,
                    "low_confidence": [],
                    "ambiguous": [
                        SlotIssue(
                            slot="metrics",
                            raw_text=raw_text,
                            reason=f"“{raw_text}”存在多个相似指标候选，缺少业务限定词，无法安全绑定唯一指标。",
                            candidates=close_candidates[: self.config.clarification_max_options],
                        )
                    ],
                    "conflict": [],
                }

        if top.score < self.config.metric_accept_score:
            return {
                "accepted": None,
                "low_confidence": [
                    SlotIssue(
                        slot="metrics",
                        raw_text=top.raw_text,
                        reason="指标候选置信度低于直接接受阈值。",
                        candidates=[top],
                    )
                ],
                "ambiguous": [],
                "conflict": [],
            }

        if second and top.score - second.score < self.config.metric_ambiguity_gap:
            return {
                "accepted": None,
                "low_confidence": [],
                "ambiguous": [
                    SlotIssue(
                        slot="metrics",
                        raw_text=top.raw_text,
                        reason="Top1 与 Top2 指标候选分数接近，无法安全绑定唯一指标。",
                        candidates=candidates[: self.config.clarification_max_options],
                    )
                ],
                "conflict": [],
            }

        return {"accepted": self._candidate_to_slot(top, context.question, top.score), "low_confidence": [], "ambiguous": [], "conflict": []}

    def _resolve_dimension(self, context: QueryUnderstandingContext) -> dict[str, Any]:
        candidates = [candidate for candidate in context.semantic_candidates if self._is_dimension_candidate(candidate)]
        matched = [
            candidate
            for candidate in candidates
            if (candidate.raw_text and candidate.raw_text in context.question)
            or candidate.display_name in context.question
            or any(alias in context.question for alias in candidate.metadata.get("aliases", []))
        ]
        matched = sorted(matched, key=lambda item: item.score, reverse=True)
        if not matched:
            return {"accepted": None, "low_confidence": [], "ambiguous": [], "conflict": []}
        raw_texts = {candidate.raw_text for candidate in matched if candidate.raw_text}
        if len(matched) > 1 and len(raw_texts) == 1:
            raw_text = next(iter(raw_texts))
            return {
                "accepted": None,
                "low_confidence": [],
                "ambiguous": [
                    SlotIssue(
                        slot="dimensions",
                        raw_text=raw_text,
                        reason=f"“{raw_text}”可能对应多个维度，无法安全绑定。",
                        candidates=matched[: self.config.clarification_max_options],
                    ),
                    SlotIssue(
                        slot="filters",
                        raw_text=raw_text,
                        reason=f"“{raw_text}”无法确认过滤字段归属。",
                        candidates=matched[: self.config.clarification_max_options],
                    )
                ],
                "conflict": [],
            }
        top = matched[0]
        if top.score < self.config.core_slot_min_confidence:
            return {
                "accepted": None,
                "low_confidence": [SlotIssue(slot="dimensions", raw_text=top.raw_text, reason="维度候选置信度不足。", candidates=[top])],
                "ambiguous": [],
                "conflict": [],
            }
        return {"accepted": self._candidate_to_slot(top, context.question, top.score), "low_confidence": [], "ambiguous": [], "conflict": []}

    @staticmethod
    def _infer_intent(question: str) -> str:
        if any(word in question for word in ("趋势", "走势", "变化", "按天", "按月")):
            return "trend_analysis"
        if any(word in question for word in ("最高", "最低", "最好", "最差", "top", "Top", "前", "后", "排名")):
            return "ranking_analysis"
        if any(word in question for word in ("同比", "环比", "对比", "较上期")):
            return "comparison_analysis"
        if any(word in question for word in ("占比", "构成", "比例")):
            return "share_analysis"
        if any(word in question for word in ("异常", "波动", "下降原因", "为什么下降")):
            return "anomaly_analysis"
        if any(word in question for word in ("明细", "详情", "列表", "清单")):
            return "detail_query"
        if any(word in question for word in ("多少", "怎么样", "查询", "看下", "统计")):
            return "metric_query"
        return "unknown"

    @staticmethod
    def _extract_time_range(question: str, current_time: datetime) -> dict[str, Any] | None:
        today = current_time.date()
        if "今天" in question:
            return {"name": "time_range", "display_name": "今天", "raw_text": "今天", "value": {"type": "relative", "start": str(today), "end": str(today)}, "confidence": 0.9, "source": "rule"}
        if "昨天" in question:
            day = today - timedelta(days=1)
            return {"name": "time_range", "display_name": "昨天", "raw_text": "昨天", "value": {"type": "relative", "start": str(day), "end": str(day)}, "confidence": 0.9, "source": "rule"}
        match = re.search(r"近\s*(\d+)\s*天", question)
        if match:
            days = int(match.group(1))
            start = today - timedelta(days=days)
            raw_text = match.group(0).replace(" ", "")
            return {"name": "time_range", "display_name": raw_text, "raw_text": raw_text, "value": {"type": "relative", "start": str(start), "end": str(today)}, "confidence": 0.88, "source": "rule"}
        if "本月" in question:
            start = today.replace(day=1)
            return {"name": "time_range", "display_name": "本月", "raw_text": "本月", "value": {"type": "relative", "start": str(start), "end": str(today)}, "confidence": 0.88, "source": "rule"}
        if "上月" in question:
            first_this_month = today.replace(day=1)
            last_month_end = first_this_month - timedelta(days=1)
            start = last_month_end.replace(day=1)
            return {"name": "time_range", "display_name": "上月", "raw_text": "上月", "value": {"type": "relative", "start": str(start), "end": str(last_month_end)}, "confidence": 0.88, "source": "rule"}
        return None

    @staticmethod
    def _extract_top_n(question: str, intent: str) -> dict[str, Any] | None:
        match = re.search(r"(?:top|Top|前|后)\s*(\d+)", question)
        if match:
            return {"name": "top_n", "display_name": match.group(0), "raw_text": match.group(0), "value": int(match.group(1)), "confidence": 0.9, "source": "rule"}
        if intent == "ranking_analysis":
            return {"name": "top_n", "display_name": "Top10", "raw_text": "", "value": 10, "confidence": 0.7, "source": "default_rule"}
        return None

    @staticmethod
    def _extract_ordering(question: str) -> dict[str, Any] | None:
        if any(word in question for word in ("最差", "最低", "最少", "后")):
            return {"name": "ordering", "display_name": "升序", "raw_text": "最差", "value": "asc", "confidence": 0.82, "source": "rule"}
        if any(word in question for word in ("最好", "最高", "最多", "前")):
            return {"name": "ordering", "display_name": "降序", "raw_text": "最好", "value": "desc", "confidence": 0.82, "source": "rule"}
        return None

    @staticmethod
    def _required_slots(intent: str) -> list[str]:
        return {
            "metric_query": ["metrics"],
            "trend_analysis": ["metrics", "time_range"],
            "ranking_analysis": ["metrics", "dimensions", "time_range"],
            "comparison_analysis": ["metrics", "time_range", "comparison"],
            "detail_query": ["entity"],
            "share_analysis": ["metrics", "dimensions", "time_range"],
            "anomaly_analysis": ["metrics", "time_range"],
        }.get(intent, [])

    @staticmethod
    def _is_metric_candidate(candidate: SlotCandidate) -> bool:
        asset_type = (candidate.asset_type or "").upper()
        return asset_type in {"", "METRIC"} or candidate.source == "metric"

    @staticmethod
    def _is_dimension_candidate(candidate: SlotCandidate) -> bool:
        asset_type = (candidate.asset_type or "").upper()
        return asset_type == "DIMENSION" or candidate.source == "dimension"

    @staticmethod
    def _find_exact_metric(question: str, candidates: list[SlotCandidate]) -> SlotCandidate | None:
        for candidate in candidates:
            terms = [candidate.display_name, candidate.metadata.get("name"), *candidate.metadata.get("aliases", [])]
            for term in terms:
                if term and term in question:
                    return candidate
        return None

    @staticmethod
    def _matched_generic_word(question: str) -> str | None:
        for word in GENERIC_METRIC_WORDS:
            if word in question:
                return word
        return None

    @staticmethod
    def _extract_natural_metric(question: str) -> dict[str, Any] | None:
        for word in NATURAL_METRIC_WORDS:
            if word in question:
                return {
                    "name": word,
                    "display_name": word,
                    "raw_text": word,
                    "confidence": 0.68,
                    "source": "rule",
                    "asset_type": None,
                    "asset_id": None,
                    "confirmed": False,
                }
        return None

    @staticmethod
    def _candidate_to_slot(candidate: SlotCandidate, question: str, confidence: float) -> dict[str, Any]:
        return {
            "name": candidate.metadata.get("name") or candidate.display_name,
            "display_name": candidate.display_name,
            "raw_text": candidate.raw_text or candidate.display_name,
            "confidence": min(confidence, 1.0),
            "source": candidate.source,
            "asset_type": candidate.asset_type,
            "asset_id": candidate.asset_id,
            "confirmed": False,
        }

    @staticmethod
    def _detect_ambiguous_time_field(context: QueryUnderstandingContext) -> SlotIssue | None:
        if not any(word in context.question for word in ("按时间", "按日期", "时间看", "日期看")):
            return None
        candidates: list[SlotCandidate] = []
        for table in context.schema_summary:
            for field in table.get("fields", []):
                name = str(field.get("name") or "")
                field_type = str(field.get("type") or "").lower()
                comment = str(field.get("comment") or "")
                haystack = f"{name} {field_type} {comment}".lower()
                if any(hint in haystack for hint in TIME_FIELD_HINTS):
                    candidates.append(
                        SlotCandidate(
                            display_name=comment or name,
                            raw_text="时间",
                            asset_type="FIELD",
                            asset_id=None,
                            score=0.75,
                            source="schema_field",
                            metadata={"table": table.get("table"), "field": name, "type": field.get("type")},
                        )
                    )
        if len(candidates) <= 1:
            return None
        return SlotIssue(slot="time_range", raw_text="时间", reason="存在多个时间字段候选，无法确认时间口径。", candidates=candidates)

    @staticmethod
    def _detect_unmapped_entity(context: QueryUnderstandingContext) -> SlotIssue | None:
        for entity in ENTITY_HINTS:
            if entity in context.question:
                return SlotIssue(slot="entity", raw_text=entity, reason=f"“{entity}”无法确认归属到哪个维度或字段。")
        return None

    @staticmethod
    def _has_issue(slot: str, *issue_groups: list[SlotIssue]) -> bool:
        return any(issue.slot == slot for issues in issue_groups for issue in issues)

    @staticmethod
    def _normalize_question(question: str, slots: dict[str, list[dict[str, Any]]]) -> str:
        return question.strip()

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))
