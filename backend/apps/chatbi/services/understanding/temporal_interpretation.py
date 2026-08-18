"""独立模型时间理解任务及旁路差异计算。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
import re
from typing import Any, Literal

import orjson
from pydantic import ValidationError

from apps.chatbi.errors import (
    QuestionModelCallError,
    QuestionModelError,
    QuestionModelOutputError,
    TemporalInterpretationError,
)
from apps.chatbi.models.dto.question_model import QuestionModelInvocationData
from apps.chatbi.models.dto.question_understanding import (
    TemporalInterpretationResult,
    TemporalShadowObservation,
    TemporalShadowStatistics,
    TimeRange,
)
from apps.chatbi.services.understanding.model_invocation import StructuredModelService
from apps.chatbi.services.understanding.prompts import (
    TEMPORAL_INTERPRETATION_SYSTEM_PROMPT,
)
from apps.temporal import (
    TemporalContext,
    TemporalError,
    TemporalPlan,
    project_time_range_payload,
    project_time_ranges_payload,
    resolve_temporal_plan,
    resolve_time_range,
    validate_temporal_plan,
)

_STAGE = "TEMPORAL_INTERPRETATION"
_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")


@dataclass(frozen=True, slots=True)
class TemporalInterpretationOutcome:
    """一次合法模型时间理解结果及其累计模型用量。"""

    plan: TemporalPlan
    usage_metadata: dict[str, int]


class TemporalInterpretationService:
    """调用模型生成时间计划，并最多执行一次结构修复。"""

    def __init__(self, question_model_service: StructuredModelService) -> None:
        self._question_model_service = question_model_service

    def interpret(
        self,
        *,
        rewritten_question: str,
        metric_mentions: list[str],
        time_mentions: list[str],
        temporal_context: TemporalContext,
        conversation_context: dict[str, Any] | None = None,
        analysis_context: dict[str, Any] | None = None,
        user_feedback: dict[str, Any] | None = None,
        user_confirmation: str | None = None,
    ) -> TemporalInterpretationOutcome:
        """生成并校验时间计划；非法输出只允许一次模型修复。"""

        usage_items: list[dict[str, Any]] = []
        validation_feedback: list[dict[str, Any]] | None = None
        last_error: ValidationError | TemporalError | None = None
        base_payload = {
            "rewritten_question": rewritten_question,
            "metric_mentions": metric_mentions,
            "time_mentions": time_mentions,
            "conversation_context": conversation_context or {},
            # 时间模型需要知道比较、占比和增长表达已经由上游识别，不能把它们当成时间词。
            "analysis_context": analysis_context or {},
            "temporal_config": {
                "reference_at": temporal_context.reference_at.isoformat(),
                "timezone": temporal_context.timezone,
                "locale": temporal_context.locale,
                "week_start": temporal_context.week_start,
                "fiscal_year_start_month": (temporal_context.fiscal_year_start_month),
                "fiscal_year_label": temporal_context.fiscal_year_label,
                "business_calendar_id": temporal_context.business_calendar_id,
            },
            "user_feedback": user_feedback or {},
            "user_confirmation": user_confirmation,
        }

        for attempt in range(2):
            current_payload = dict(base_payload)
            if validation_feedback is not None:
                current_payload["repair_feedback"] = {
                    "reason_code": "TEMPORAL_MODEL_OUTPUT_INVALID",
                    "validation_errors": validation_feedback,
                    "instruction": (
                        "只修复字段结构、来源位置和时间计划约束，不得改变原问题语义。"
                    ),
                }
            try:
                result = self._question_model_service.invoke(
                    QuestionModelInvocationData(
                        stage=_STAGE,
                        system_prompt=TEMPORAL_INTERPRETATION_SYSTEM_PROMPT,
                        user_prompt=orjson.dumps(current_payload).decode(),
                    )
                )
            except QuestionModelCallError as exc:
                raise TemporalInterpretationError(
                    "TEMPORAL_MODEL_CALL_FAILED",
                    usage_metadata=_merge_usage(*usage_items),
                ) from exc
            except QuestionModelOutputError as exc:
                repaired_plan = _repair_plan_from_time_mentions(
                    time_mentions=time_mentions,
                    metric_mentions=metric_mentions,
                    rewritten_question=rewritten_question,
                    temporal_context=temporal_context,
                    analysis_context=analysis_context,
                )
                if repaired_plan is not None:
                    repaired_plan = _normalize_custom_comparison_order(repaired_plan)
                    validate_temporal_plan(
                        repaired_plan,
                        rewritten_question=rewritten_question,
                        user_confirmation=user_confirmation,
                        metric_mentions=tuple(metric_mentions),
                    )
                    return TemporalInterpretationOutcome(
                        plan=repaired_plan,
                        usage_metadata=_merge_usage(*usage_items),
                    )
                output_code = exc.code.removeprefix("QUESTION_MODEL_")
                raise TemporalInterpretationError(
                    f"TEMPORAL_MODEL_{output_code}",
                    usage_metadata=_merge_usage(*usage_items),
                ) from exc
            except QuestionModelError as exc:
                raise TemporalInterpretationError(
                    "TEMPORAL_MODEL_INVOCATION_INVALID",
                    usage_metadata=_merge_usage(*usage_items),
                ) from exc

            usage_items.append(result.usage_metadata)
            plan: TemporalPlan | None = None
            try:
                try:
                    plan = TemporalPlan.model_validate(result.payload)
                except ValidationError:
                    # 模型输出结构损坏时，仍先尝试对上游已识别的明确时间表达做确定性解析。
                    plan = _repair_plan_from_time_mentions(
                        time_mentions=time_mentions,
                        metric_mentions=metric_mentions,
                        rewritten_question=rewritten_question,
                        temporal_context=temporal_context,
                        analysis_context=analysis_context,
                    )
                    if plan is None:
                        raise
                repaired_plan = _repair_plan_from_time_mentions(
                    plan=plan,
                    time_mentions=time_mentions,
                    metric_mentions=metric_mentions,
                    rewritten_question=rewritten_question,
                    temporal_context=temporal_context,
                    analysis_context=analysis_context,
                )
                if repaired_plan is not None:
                    plan = repaired_plan
                plan = _normalize_custom_comparison_order(plan)
                validate_temporal_plan(
                    plan,
                    rewritten_question=rewritten_question,
                    user_confirmation=user_confirmation,
                    metric_mentions=tuple(metric_mentions),
                )
                return TemporalInterpretationOutcome(
                    plan=plan,
                    usage_metadata=_merge_usage(*usage_items),
                )
            except (ValidationError, TemporalError) as exc:
                # 模型可能生成结构合法但时间关系错误的 resolved 计划；这类错误也要回到
                # 已确认的明确时间表达重新构造，而不能只把错误交给第二次模型修复。
                repaired_plan = _repair_plan_from_time_mentions(
                    plan=plan,
                    time_mentions=time_mentions,
                    metric_mentions=metric_mentions,
                    rewritten_question=rewritten_question,
                    temporal_context=temporal_context,
                    analysis_context=analysis_context,
                    force=True,
                )
                if repaired_plan is not None:
                    repaired_plan = _normalize_custom_comparison_order(repaired_plan)
                    try:
                        validate_temporal_plan(
                            repaired_plan,
                            rewritten_question=rewritten_question,
                            user_confirmation=user_confirmation,
                            metric_mentions=tuple(metric_mentions),
                        )
                    except (ValidationError, TemporalError):
                        pass
                    else:
                        return TemporalInterpretationOutcome(
                            plan=repaired_plan,
                            usage_metadata=_merge_usage(*usage_items),
                        )
                last_error = exc
                validation_feedback = _validation_errors(exc)
                if attempt == 0:
                    continue

        assert last_error is not None
        raise TemporalInterpretationError(
            "TEMPORAL_MODEL_OUTPUT_INVALID",
            usage_metadata=_merge_usage(*usage_items),
        ) from last_error

    def interpret_for_execution(
        self,
        *,
        rewritten_question: str,
        metric_mentions: list[str],
        time_mentions: list[str],
        temporal_context: TemporalContext,
        conversation_context: dict[str, Any] | None = None,
        analysis_context: dict[str, Any] | None = None,
        user_feedback: dict[str, Any] | None = None,
        user_confirmation: str | None = None,
    ) -> tuple[TemporalInterpretationResult, dict[str, int]]:
        """生成权威时间计划；可执行状态必须完成确定性解析。"""

        outcome = self.interpret(
            rewritten_question=rewritten_question,
            metric_mentions=metric_mentions,
            time_mentions=time_mentions,
            temporal_context=temporal_context,
            conversation_context=conversation_context,
            analysis_context=analysis_context,
            user_feedback=user_feedback,
            user_confirmation=user_confirmation,
        )
        return (
            _resolve_execution_result(
                plan=outcome.plan,
                temporal_context=temporal_context,
                rewritten_question=rewritten_question,
                user_confirmation=user_confirmation,
                usage_metadata=outcome.usage_metadata,
            ),
            outcome.usage_metadata,
        )

    def resolve_confirmed_plan(
        self,
        *,
        plan: TemporalPlan | dict[str, Any],
        rewritten_question: str,
        metric_mentions: list[str],
        temporal_context: TemporalContext,
        user_confirmation: str,
        allowed_ambiguity_codes: set[str],
    ) -> TemporalInterpretationResult:
        """校验并解析服务端选项携带的计划，不再次调用模型。"""

        try:
            validated_plan = TemporalPlan.model_validate(plan)
            validate_temporal_plan(
                validated_plan,
                rewritten_question=rewritten_question,
                user_confirmation=user_confirmation,
                metric_mentions=tuple(metric_mentions),
            )
        except (ValidationError, TemporalError) as exc:
            raise TemporalInterpretationError(
                "TEMPORAL_CONFIRMATION_PLAN_INVALID"
            ) from exc
        allowed_values = [
            option["value"]
            for option in build_temporal_clarification_options(allowed_ambiguity_codes)
        ]
        confirmed_payload = validated_plan.model_dump(mode="json")
        if not any(
            value.get("temporal_confirmation") == user_confirmation
            and value.get("temporal_plan") == confirmed_payload
            for value in allowed_values
        ):
            raise TemporalInterpretationError(
                "TEMPORAL_CONFIRMATION_OPTION_NOT_ALLOWED"
            )
        if validated_plan.status not in {"resolved", "no_time"}:
            raise TemporalInterpretationError("TEMPORAL_CONFIRMATION_PLAN_UNRESOLVED")
        return _resolve_execution_result(
            plan=validated_plan,
            temporal_context=temporal_context,
            rewritten_question=rewritten_question,
            user_confirmation=user_confirmation,
            usage_metadata={},
        )


def compare_temporal_shadow(
    *,
    plan: TemporalPlan,
    legacy_time_range: TimeRange,
    temporal_context: TemporalContext,
) -> TemporalShadowObservation:
    """解析模型计划，并与当前权威时间范围逐字段对照。"""

    if plan.status in {"clarification_required", "unsupported"}:
        return TemporalShadowObservation(
            status="not_comparable",
            plan=plan,
            legacy_time_range=legacy_time_range,
            difference_codes=(f"model_plan_{plan.status}",),
        )

    try:
        resolved_plan = resolve_temporal_plan(plan, temporal_context)
    except TemporalError as exc:
        return TemporalShadowObservation(
            status="resolution_error",
            plan=plan,
            legacy_time_range=legacy_time_range,
            error_code=exc.code,
        )

    candidate_time_range = TimeRange.model_validate(
        project_time_ranges_payload(resolved_plan)[0]
        if resolved_plan.filters
        else project_time_range_payload(resolved_plan)
    )
    difference_codes = _time_range_differences(
        candidate_time_range,
        legacy_time_range,
    )
    return TemporalShadowObservation(
        status="different" if difference_codes else "matched",
        plan=plan,
        resolved_plan=resolved_plan,
        legacy_time_range=legacy_time_range,
        difference_codes=tuple(difference_codes),
    )


def apply_temporal_interpretation_payload(
    intent: dict[str, Any],
    temporal_interpretation: TemporalInterpretationResult,
) -> dict[str, Any]:
    """将权威时间结果投影到 Agent 与 Graph 共用的自然语言意图结构。"""

    plan = temporal_interpretation.plan
    query_shape = dict(intent.get("query_shape") or {})
    # 先清除旧时间投影，确保 TemporalPlan 是唯一时间语义来源。
    query_shape.pop("time_grain", None)
    query_shape.pop("comparison_type", None)
    required_slot_types = [
        slot
        for slot in intent.get("required_slot_types") or []
        if slot not in {"time_dimension", "time_range"}
    ]
    if plan.grouping is not None:
        query_shape["time_grain"] = plan.grouping.grain
        query_shape["needs_group_by"] = True
    if (
        temporal_interpretation.time_range.value_status == "provided"
        or plan.grouping is not None
    ) and "time_dimension" not in required_slot_types:
        required_slot_types.append("time_dimension")
    comparison = None
    if plan.comparison is not None:
        base_raw = plan.comparison.base
        if base_raw is None and plan.expressions:
            base_raw = next(
                (
                    expression.raw
                    for expression in plan.expressions
                    if expression.role == "query_filter"
                ),
                None,
            )
        comparison = {
            "base": base_raw,
            "compare": list(plan.comparison.compare),
            "method": plan.comparison.method,
        }
        query_shape["comparison_type"] = plan.comparison.method
    temporal_conflict_slots = {
        "time",
        "time_range",
        "time_grain",
        "时间",
        "时间范围",
        "时间粒度",
    }
    return {
        **intent,
        "time_range": temporal_interpretation.time_range.model_dump(mode="json"),
        "time_ranges": [
            item.model_dump(mode="json")
            for item in temporal_interpretation.time_ranges
        ],
        "comparison": comparison,
        "time_mentions": list(
            dict.fromkeys(expression.raw for expression in plan.expressions)
        ),
        "query_shape": query_shape,
        "required_slot_types": required_slot_types,
        "conflict_slots": [
            slot
            for slot in intent.get("conflict_slots") or []
            if str(slot).strip().lower() not in temporal_conflict_slots
        ],
    }


def build_temporal_clarification_options(
    ambiguity_codes: set[str],
) -> list[dict[str, Any]]:
    """为缺少数量或单位的时间歧义生成可直接校验的公共选项。"""

    if not ambiguity_codes.intersection(
        {"time_range_amount_missing", "time_range_unit_missing"}
    ):
        return []
    candidates = (
        (
            "最近 7 天",
            "最近7天",
            {
                "kind": "rolling_range",
                "direction": "past",
                "amount": 7,
                "unit": "day",
                "include_reference_date": True,
            },
        ),
        (
            "最近 30 天",
            "最近30天",
            {
                "kind": "rolling_range",
                "direction": "past",
                "amount": 30,
                "unit": "day",
                "include_reference_date": True,
            },
        ),
        (
            "本月",
            "本月",
            {
                "kind": "calendar_period",
                "unit": "month",
                "offset": 0,
            },
        ),
    )
    return [
        {
            "label": label,
            "value": {
                "temporal_confirmation": confirmation,
                "temporal_plan": {
                    "schema_version": "1",
                    "status": "resolved",
                    "expressions": [
                        {
                            **expression,
                            "raw": confirmation,
                            "source": "user_confirmation",
                            "start_offset": 0,
                            "end_offset": len(confirmation),
                            "role": "query_filter",
                        }
                    ],
                    "grouping": None,
                    "comparison": None,
                    "ambiguities": [],
                    "confidence": 1.0,
                },
            },
        }
        for label, confirmation, expression in candidates
    ]


def _resolve_execution_result(
    *,
    plan: TemporalPlan,
    temporal_context: TemporalContext,
    rewritten_question: str,
    user_confirmation: str | None,
    usage_metadata: dict[str, int],
) -> TemporalInterpretationResult:
    """将合法时间计划解析为统一执行前结果。"""

    resolved_plan = None
    time_range = TimeRange()
    time_ranges: list[TimeRange] = []
    if plan.status in {"resolved", "no_time"}:
        try:
            resolved_plan = resolve_temporal_plan(
                plan,
                temporal_context,
                rewritten_question=rewritten_question,
                user_confirmation=user_confirmation,
            )
            time_ranges = [
                TimeRange.model_validate(item)
                for item in project_time_ranges_payload(resolved_plan)
            ]
            time_range = time_ranges[0] if time_ranges else TimeRange()
        except TemporalError as exc:
            raise TemporalInterpretationError(
                exc.code,
                usage_metadata=usage_metadata,
            ) from exc
    interpretation_source: Literal["model", "user_confirmation"] = (
        "user_confirmation" if user_confirmation else "model"
    )
    time_range = time_range.model_copy(
        update={"interpretation_source": interpretation_source}
    )
    time_ranges = [
        item.model_copy(update={"interpretation_source": interpretation_source})
        for item in time_ranges
    ]
    if time_range.value_status == "provided" and not time_ranges:
        time_ranges = [time_range]
    return TemporalInterpretationResult(
        plan=plan,
        resolved_plan=resolved_plan,
        time_range=time_range,
        time_ranges=time_ranges,
        interpretation_source=interpretation_source,
    )


def summarize_temporal_shadow_observations(
    observations: Iterable[TemporalShadowObservation],
) -> TemporalShadowStatistics:
    """汇总旁路状态、字段差异和错误，供离线准入评估使用。"""

    items = tuple(observations)
    status_counts = Counter(item.status for item in items)
    comparable_count = status_counts["matched"] + status_counts["different"]
    matched_count = status_counts["matched"]
    difference_counts = Counter(
        code for item in items for code in item.difference_codes
    )
    error_counts = Counter(
        item.error_code for item in items if item.error_code is not None
    )
    plan_status_counts = Counter(
        item.plan.status for item in items if item.plan is not None
    )
    ambiguity_counts = Counter(
        ambiguity.code
        for item in items
        if item.plan is not None
        for ambiguity in item.plan.ambiguities
    )
    return TemporalShadowStatistics(
        observation_count=len(items),
        status_counts={str(status): count for status, count in status_counts.items()},
        comparable_count=comparable_count,
        matched_count=matched_count,
        match_rate=(matched_count / comparable_count if comparable_count else None),
        difference_counts=dict(difference_counts),
        error_counts=dict(error_counts),
        plan_status_counts={
            str(status): count for status, count in plan_status_counts.items()
        },
        ambiguity_counts={str(code): count for code, count in ambiguity_counts.items()},
    )


def _validation_errors(
    error: ValidationError | TemporalError,
) -> list[dict[str, Any]]:
    if isinstance(error, ValidationError):
        return [
            {
                "type": item.get("type"),
                "loc": list(item.get("loc") or ()),
                "msg": item.get("msg"),
            }
            for item in error.errors(
                include_url=False,
                include_input=False,
            )
        ]
    return [{"type": "business_rule", "loc": [], "msg": error.code}]


def _repair_plan_from_time_mentions(
    *,
    time_mentions: list[str],
    metric_mentions: list[str],
    rewritten_question: str,
    temporal_context: TemporalContext,
    analysis_context: dict[str, Any] | None,
    plan: TemporalPlan | None = None,
    force: bool = False,
) -> TemporalPlan | None:
    """用确定性时间解析接住模型未识别但上游已确认的明确时间表达。"""

    if (
        plan is not None
        and plan.status not in {"clarification_required", "unsupported"}
        and not force
    ):
        return None
    if plan is not None and any(
        expression.role == "metric_definition"
        and any(
            expression.raw in metric and expression.raw != metric
            for metric in metric_mentions
        )
        for expression in plan.expressions
    ):
        # 指标内部时间由模型计划保留；这里只修复查询时间，不能覆盖指标口径。
        return None
    mentions = _coalesce_time_mentions(time_mentions, rewritten_question)
    if not mentions or not all(_is_explicit_calendar_mention(raw) for raw in mentions):
        return None
    explicit_year = _first_explicit_year(mentions)
    expression_payloads: list[dict[str, Any]] = []
    for raw in mentions:
        parse_raw = _inherit_explicit_year(raw, explicit_year)
        normalized = resolve_time_range(parse_raw, temporal_context)
        if not normalized or normalized.get("kind") != "absolute_range":
            return None
        try:
            start = date.fromisoformat(str(normalized["start"]))
            end_exclusive = date.fromisoformat(str(normalized["end_exclusive"]))
        except (KeyError, TypeError, ValueError):
            return None
        expression_payloads.append(
            {
                **_absolute_expression_payload(
                    raw=raw,
                    start=start,
                    end_exclusive=end_exclusive,
                ),
                "role": "query_filter",
                "source": "rewritten_question",
            }
        )

    context = analysis_context if isinstance(analysis_context, dict) else {}
    comparison = context.get("comparison")
    comparison = comparison if isinstance(comparison, dict) else None
    analysis_expressions = context.get("expressions")
    analysis_expressions = (
        [item for item in analysis_expressions if isinstance(item, dict)]
        if isinstance(analysis_expressions, list)
        else []
    )
    analysis_ops = {str(item.get("op") or "") for item in analysis_expressions}
    intent_type = str(context.get("intent_type") or "")
    if comparison is None and (
        len(expression_payloads) >= 2
        and (
            intent_type == "comparison_analysis"
            or analysis_ops.intersection({"growth", "compare", "diff"})
        )
    ):
        comparison = {
            "method": "custom",
            "base": mentions[0],
            "compare": mentions[1:],
        }
    elif comparison is None and len(expression_payloads) == 1:
        if "同比" in rewritten_question:
            comparison = {"method": "yoy", "base": mentions[0], "compare": []}
        elif "环比" in rewritten_question:
            comparison = {"method": "mom", "base": mentions[0], "compare": []}

    payload: dict[str, Any] = {
        "schema_version": "1",
        "status": "resolved",
        "expressions": expression_payloads,
        "grouping": None,
        "comparison": comparison,
        "ambiguities": [],
        "confidence": 0.9,
    }
    try:
        return TemporalPlan.model_validate(payload)
    except ValidationError:
        return None


def _coalesce_time_mentions(
    time_mentions: list[str],
    rewritten_question: str,
) -> list[str]:
    """合并连续的时间提及，例如“2026年”与“上半年”。"""

    result: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    previous_end: int | None = None
    for value in time_mentions:
        raw = str(value or "").strip()
        if not raw:
            continue
        start = rewritten_question.find(raw, cursor)
        if start < 0:
            result.append(raw)
            previous_end = None
            continue
        end = start + len(raw)
        if result and previous_end == start:
            previous_start = spans[-1][0]
            result[-1] = rewritten_question[previous_start:end]
            spans[-1] = (previous_start, end)
        else:
            result.append(raw)
            spans.append((start, end))
        cursor = end
        previous_end = end
    return result


def _first_explicit_year(mentions: list[str]) -> int | None:
    for raw in mentions:
        match = re.search(r"(?<!\d)(\d{4})年", raw)
        if match:
            return int(match.group(1))
    return None


def _inherit_explicit_year(raw: str, year: int | None) -> str:
    if year is None or re.search(r"(?<!\d)\d{4}年", raw):
        return raw
    if re.search(r"\d{1,2}月", raw) or raw in {"上半年", "下半年"}:
        return f"{year}年{raw}"
    return raw


def _is_explicit_calendar_mention(raw: str) -> bool:
    """只把原文明确给出的日、月、年和半年交给确定性解析。"""

    return bool(
        re.search(r"(?<!\d)\d{4}年", raw)
        or re.search(r"\d{1,2}月", raw)
        or re.search(r"\d{1,2}日", raw)
        or raw in {"上半年", "下半年"}
    )


def _normalize_custom_comparison_order(plan: TemporalPlan) -> TemporalPlan:
    """按原文查询时间顺序统一自定义比较的基期与对比期。"""

    if plan.comparison is None or plan.comparison.method != "custom":
        return plan
    query_filters = [
        expression
        for expression in plan.expressions
        if expression.role == "query_filter"
    ]
    if len(query_filters) < 2:
        return plan
    return plan.model_copy(
        update={
            "comparison": plan.comparison.model_copy(
                update={
                    "base": query_filters[0].raw,
                    "compare": tuple(
                        expression.raw for expression in query_filters[1:]
                    ),
                }
            )
        }
    )


def _absolute_expression_payload(
    *,
    raw: str,
    start: date,
    end_exclusive: date,
) -> dict[str, Any]:
    if end_exclusive == start + timedelta(days=1):
        return {
            "kind": "absolute_date",
            "raw": raw,
            "date": start.isoformat(),
        }
    return {
        "kind": "absolute_range",
        "raw": raw,
        "start": start.isoformat(),
        "end_inclusive": (end_exclusive - timedelta(days=1)).isoformat(),
    }


def _time_range_differences(
    candidate: TimeRange,
    legacy: TimeRange,
) -> list[str]:
    differences: list[str] = []
    if candidate.value_status != legacy.value_status:
        return ["time_presence"]
    if candidate.value_status == "not_provided":
        return differences
    if candidate.raw != legacy.raw:
        differences.append("source_raw")

    candidate_range = candidate.normalized or {}
    legacy_range = legacy.normalized or {}
    for field in ("kind", "start", "end_exclusive", "timezone"):
        if candidate_range.get(field) != legacy_range.get(field):
            differences.append(f"range_{field}")
    return differences


def _merge_usage(*items: dict[str, Any]) -> dict[str, int]:
    return {key: sum(int(item.get(key) or 0) for item in items) for key in _USAGE_KEYS}


__all__ = [
    "TemporalInterpretationOutcome",
    "TemporalInterpretationResult",
    "TemporalInterpretationService",
    "apply_temporal_interpretation_payload",
    "build_temporal_clarification_options",
    "compare_temporal_shadow",
    "summarize_temporal_shadow_observations",
]
