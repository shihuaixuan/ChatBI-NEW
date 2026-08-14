"""把自然语言时间表达解析为受控时间结构。"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Literal

from apps.temporal.calendar import (
    fiscal_period_label,
    fiscal_quarter_bounds,
    fiscal_year_bounds,
    named_fiscal_quarter_bounds,
    named_fiscal_year_bounds,
    period_bounds,
    shift_months,
)
from apps.temporal.errors import (
    TemporalPlanResolutionError,
    TemporalPlanValidationError,
)
from apps.temporal.extractor import normalize_time_text
from apps.temporal.jionlp_adapter import parse_jionlp_time
from apps.temporal.models import TemporalContext, build_temporal_context
from apps.temporal.plan import (
    AbsoluteDateExpression,
    AbsoluteRangeExpression,
    CalendarPeriodExpression,
    FiscalPeriodExpression,
    RelativeDateExpression,
    ResolvedTemporalPlan,
    ResolvedTemporalRange,
    RollingRangeExpression,
    TemporalExpression,
    TemporalPlan,
    validate_temporal_plan,
)


def resolve_time_range(
    raw: Any,
    temporal_context: TemporalContext,
) -> dict[str, Any] | None:
    """使用 JioNLP 和固定 Run 基准时间解析时间表达。"""

    text = normalize_time_text(raw)
    if not text:
        return None
    if text in {"本周", "上周"} and temporal_context.week_start != "monday":
        start, end_exclusive = period_bounds(
            temporal_context.reference_date,
            "week",
            temporal_context.week_start,
        )
        if text == "上周":
            start, end_exclusive = period_bounds(
                start - timedelta(days=1),
                "week",
                temporal_context.week_start,
            )
        return _absolute_range(start, end_exclusive, temporal_context.timezone, raw)
    return parse_jionlp_time(raw, temporal_context)


def resolve_time_range_payload(
    time_range: dict[str, Any],
    temporal_context: TemporalContext,
) -> dict[str, Any]:
    """补齐可信绝对范围；已有绝对结果保持不变，便于恢复和重复投影。"""

    if str(time_range.get("value_status") or "").lower() != "provided":
        return time_range
    existing = time_range.get("normalized")
    if isinstance(existing, dict) and existing.get("kind") == "absolute_range":
        return time_range
    normalized = resolve_time_range(time_range.get("raw"), temporal_context)
    if normalized is None:
        return time_range
    return {**time_range, "normalized": normalized}


def resolve_temporal_plan(
    plan: TemporalPlan,
    temporal_context: TemporalContext,
    *,
    rewritten_question: str | None = None,
    user_confirmation: str | None = None,
    max_span_days: int | None = None,
) -> ResolvedTemporalPlan:
    """校验结构化计划，并使用固定 Run 上下文生成可信绝对范围。"""

    validate_temporal_plan(
        plan,
        rewritten_question=rewritten_question,
        user_confirmation=user_confirmation,
    )
    if max_span_days is not None and max_span_days <= 0:
        raise TemporalPlanValidationError("TEMPORAL_MAX_SPAN_DAYS_INVALID")
    if plan.status == "no_time":
        return ResolvedTemporalPlan(status="no_time")
    if plan.status != "resolved":
        raise TemporalPlanResolutionError(f"TEMPORAL_PLAN_STATUS_{plan.status.upper()}")

    try:
        filters = tuple(
            _resolve_temporal_expression(expression, temporal_context)
            for expression in plan.expressions
            if expression.role == "query_filter"
        )
    except (OverflowError, ValueError) as exc:
        if isinstance(exc, TemporalPlanResolutionError):
            raise
        raise TemporalPlanResolutionError() from exc

    if max_span_days is not None and any(
        (item.end_exclusive - item.start).days > max_span_days for item in filters
    ):
        raise TemporalPlanValidationError("TEMPORAL_PLAN_MAX_SPAN_EXCEEDED")
    return ResolvedTemporalPlan(
        status="resolved",
        filters=filters,
        grouping=plan.grouping,
    )


def project_time_range_payload(
    resolved_plan: ResolvedTemporalPlan,
) -> dict[str, Any]:
    """把权威解析计划投影成现有下游使用的兼容 TimeRange。"""

    if not resolved_plan.filters:
        return {"raw": None, "value_status": "not_provided"}
    if len(resolved_plan.filters) != 1:
        raise TemporalPlanValidationError("TEMPORAL_PLAN_QUERY_FILTER_CONFLICT")
    resolved_range = resolved_plan.filters[0]
    normalized = resolved_range.model_dump(
        mode="json",
        exclude_none=True,
        exclude={"role"},
    )
    if resolved_range.calendar == "natural":
        normalized.pop("calendar", None)
    return {
        "raw": resolved_range.source_raw,
        "value_status": "provided",
        "normalized": normalized,
    }


def normalize_time_range(
    raw: Any,
    timezone: str = "Asia/Shanghai",
    *,
    temporal_context: TemporalContext | None = None,
) -> dict[str, Any] | None:
    """兼容无 Run 上下文的旧投影，统一使用 JioNLP 结果。"""

    if temporal_context is not None:
        return resolve_time_range(raw, temporal_context)
    text = normalize_time_text(raw)
    if not text:
        return None
    # 无 Run 上下文时保留符号化结果，避免在模型投影阶段读取当前日期。
    single_dates = {
        "今天": 0,
        "今日": 0,
        "today": 0,
        "昨天": -1,
        "昨日": -1,
        "yesterday": -1,
        "明天": 1,
        "tomorrow": 1,
    }
    if text.lower() in single_dates:
        return {
            "kind": "single_date",
            "anchor": "today",
            "offset_days": single_dates[text.lower()],
            "timezone": timezone,
        }
    recent = re.fullmatch(r"(?:最近|近)(\d+)(天|日|周|个月|月|年)", text)
    if recent:
        unit = {
            "天": "day",
            "日": "day",
            "周": "week",
            "个月": "month",
            "月": "month",
            "年": "year",
        }[recent.group(2)]
        return {
            "kind": "relative_range",
            "unit": unit,
            "amount": int(recent.group(1)),
            "anchor": "today",
            "include_current": True,
            "timezone": timezone,
        }
    if text.endswith(("每天", "每日", "按天", "按日")):
        without_grain = text[:-2]
        recent = re.fullmatch(r"(?:最近|近)(\d+)(天|日|周|个月|月|年)", without_grain)
        if recent:
            return {
                "kind": "relative_range",
                "unit": "day",
                "amount": int(recent.group(1)),
                "anchor": "today",
                "include_current": True,
                "timezone": timezone,
            }
    parsed = parse_jionlp_time(raw, build_temporal_context(timezone=timezone))
    if isinstance(parsed, dict):
        parsed.pop("source_raw", None)
    return parsed


def normalize_time_range_payload(
    time_range: dict[str, Any],
    *,
    temporal_context: TemporalContext | None = None,
) -> dict[str, Any]:
    """统一补齐 JioNLP 解析结果，并保留已有 Run 事实。"""

    if temporal_context is not None:
        resolved = resolve_time_range_payload(time_range, temporal_context)
        if str(resolved.get("value_status") or "").lower() != "provided":
            return resolved
        return {
            **resolved,
            "interpretation_source": (
                time_range.get("interpretation_source") or "jionlp"
            ),
        }
    if str(time_range.get("value_status") or "").lower() != "provided":
        return time_range
    existing = time_range.get("normalized")
    if isinstance(existing, dict):
        # 已固定的结果属于 Run 事实，后续投影不得重新解释。
        return time_range
    normalized = normalize_time_range(time_range.get("raw"))
    if normalized is None:
        return time_range
    return {
        **time_range,
        "normalized": normalized,
        "interpretation_source": (
            time_range.get("interpretation_source") or "jionlp"
        ),
    }


def _absolute_range(
    start: date,
    end_exclusive: date,
    timezone: str,
    source_raw: Any,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "kind": "absolute_range",
        "start": start.isoformat(),
        "end_exclusive": end_exclusive.isoformat(),
        "timezone": timezone,
        "source_raw": str(source_raw),
    }
    if metadata:
        result.update(metadata)
    return result


def _resolve_temporal_expression(
    expression: TemporalExpression,
    temporal_context: TemporalContext,
) -> ResolvedTemporalRange:
    reference_date = temporal_context.reference_date
    if isinstance(expression, AbsoluteDateExpression):
        start = expression.date
        return _resolved_temporal_range(
            expression,
            start,
            start + timedelta(days=1),
            temporal_context,
        )
    if isinstance(expression, AbsoluteRangeExpression):
        return _resolved_temporal_range(
            expression,
            expression.start,
            expression.end_inclusive + timedelta(days=1),
            temporal_context,
        )
    if isinstance(expression, RelativeDateExpression):
        start = reference_date + timedelta(days=expression.offset_days)
        return _resolved_temporal_range(
            expression,
            start,
            start + timedelta(days=1),
            temporal_context,
        )
    if isinstance(expression, RollingRangeExpression):
        start, end_exclusive = _rolling_range_bounds(expression, reference_date)
        return _resolved_temporal_range(
            expression,
            start,
            end_exclusive,
            temporal_context,
        )
    if isinstance(expression, CalendarPeriodExpression):
        period_anchor = _shift_period_anchor(
            reference_date,
            expression.unit,
            expression.offset,
        )
        start, end_exclusive = period_bounds(
            period_anchor,
            expression.unit,
            temporal_context.week_start,
        )
        return _resolved_temporal_range(
            expression,
            start,
            end_exclusive,
            temporal_context,
        )
    if isinstance(expression, FiscalPeriodExpression):
        return _resolve_structured_fiscal_period(expression, temporal_context)
    raise TemporalPlanResolutionError("TEMPORAL_EXPRESSION_KIND_UNSUPPORTED")


def _rolling_range_bounds(
    expression: RollingRangeExpression,
    reference_date: date,
) -> tuple[date, date]:
    if expression.unit in {"day", "week"}:
        day_count = expression.amount * (7 if expression.unit == "week" else 1)
        if expression.direction == "past":
            end_exclusive = reference_date + timedelta(
                days=1 if expression.include_reference_date else 0
            )
            return end_exclusive - timedelta(days=day_count), end_exclusive
        start = reference_date + timedelta(
            days=0 if expression.include_reference_date else 1
        )
        return start, start + timedelta(days=day_count)

    month_count = expression.amount * (12 if expression.unit == "year" else 1)
    if expression.direction == "past":
        if expression.include_reference_date:
            return (
                shift_months(reference_date, -month_count) + timedelta(days=1),
                reference_date + timedelta(days=1),
            )
        return shift_months(reference_date, -month_count), reference_date
    if expression.include_reference_date:
        return reference_date, shift_months(reference_date, month_count)
    return (
        reference_date + timedelta(days=1),
        shift_months(reference_date, month_count) + timedelta(days=1),
    )


def _shift_period_anchor(anchor: date, unit: str, offset: int) -> date:
    if unit == "week":
        return anchor + timedelta(days=offset * 7)
    month_factor = {"month": 1, "quarter": 3, "year": 12}.get(unit)
    if month_factor is None:
        raise TemporalPlanResolutionError("TEMPORAL_PERIOD_UNIT_UNSUPPORTED")
    return shift_months(anchor, offset * month_factor)


def _resolve_structured_fiscal_period(
    expression: FiscalPeriodExpression,
    temporal_context: TemporalContext,
) -> ResolvedTemporalRange:
    if expression.fiscal_year is not None:
        if expression.unit == "year":
            start, end_exclusive = named_fiscal_year_bounds(
                expression.fiscal_year,
                temporal_context.fiscal_year_start_month,
                temporal_context.fiscal_year_label,
            )
        else:
            assert expression.fiscal_quarter is not None
            start, end_exclusive = named_fiscal_quarter_bounds(
                expression.fiscal_year,
                expression.fiscal_quarter,
                temporal_context.fiscal_year_start_month,
                temporal_context.fiscal_year_label,
            )
    else:
        bounds = (
            fiscal_year_bounds if expression.unit == "year" else fiscal_quarter_bounds
        )
        start, end_exclusive = bounds(
            temporal_context.reference_date,
            temporal_context.fiscal_year_start_month,
        )
        month_count = 12 if expression.unit == "year" else 3
        start = shift_months(start, expression.offset * month_count)
        end_exclusive = shift_months(
            end_exclusive,
            expression.offset * month_count,
        )

    fiscal_year, fiscal_quarter = fiscal_period_label(
        start,
        temporal_context.fiscal_year_start_month,
        temporal_context.fiscal_year_label,
    )
    return _resolved_temporal_range(
        expression,
        start,
        end_exclusive,
        temporal_context,
        calendar="fiscal",
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter if expression.unit == "quarter" else None,
    )


def _resolved_temporal_range(
    expression: TemporalExpression,
    start: date,
    end_exclusive: date,
    temporal_context: TemporalContext,
    *,
    calendar: Literal["natural", "fiscal"] = "natural",
    fiscal_year: int | None = None,
    fiscal_quarter: int | None = None,
) -> ResolvedTemporalRange:
    fiscal = calendar == "fiscal"
    return ResolvedTemporalRange(
        start=start,
        end_exclusive=end_exclusive,
        timezone=temporal_context.timezone,
        source_raw=expression.raw,
        calendar=calendar,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        fiscal_year_start_month=(
            temporal_context.fiscal_year_start_month if fiscal else None
        ),
        fiscal_year_label=temporal_context.fiscal_year_label if fiscal else None,
    )


__all__ = [
    "normalize_time_range",
    "normalize_time_range_payload",
    "project_time_range_payload",
    "resolve_temporal_plan",
    "resolve_time_range",
    "resolve_time_range_payload",
]
