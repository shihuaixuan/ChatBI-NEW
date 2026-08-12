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
from apps.temporal.extractor import ABSOLUTE_DATE_PATTERN, normalize_time_text
from apps.temporal.models import TemporalContext
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

_CURRENT_FISCAL_PERIODS = {
    "本财年": "year",
    "当前财年": "year",
    "本财政年度": "year",
    "本财季": "quarter",
    "当前财季": "quarter",
    "本财政季度": "quarter",
}
_PREVIOUS_FISCAL_PERIODS = {
    "上财年": "year",
    "上一财年": "year",
    "上个财年": "year",
    "上一财政年度": "year",
    "上财季": "quarter",
    "上一财季": "quarter",
    "上个财季": "quarter",
    "上一财政季度": "quarter",
}


def resolve_time_range(
    raw: Any,
    temporal_context: TemporalContext,
) -> dict[str, Any] | None:
    """使用固定基准时间解析时间表达，不在下游再次读取当前日期。"""

    text = normalize_time_text(raw)
    if not text:
        return None
    timezone = temporal_context.timezone
    reference_date = temporal_context.reference_date
    lowered = text.lower()

    single_date_offsets = {
        "今天": 0,
        "今日": 0,
        "today": 0,
        "昨天": -1,
        "昨日": -1,
        "yesterday": -1,
        "明天": 1,
        "tomorrow": 1,
    }
    if lowered in single_date_offsets:
        start = reference_date + timedelta(days=single_date_offsets[lowered])
        return _absolute_range(start, start + timedelta(days=1), timezone, raw)

    matched_range = re.fullmatch(
        rf"{ABSOLUTE_DATE_PATTERN}(?:至|到|~|～|—|–){ABSOLUTE_DATE_PATTERN}",
        text,
    )
    if matched_range:
        parts = matched_range.groups()
        parsed_start = _date_from_parts(parts[:3])
        inclusive_end = _date_from_parts(parts[3:])
        if (
            parsed_start is None
            or inclusive_end is None
            or inclusive_end < parsed_start
        ):
            return _unsupported(raw, timezone)
        return _absolute_range(
            parsed_start,
            inclusive_end + timedelta(days=1),
            timezone,
            raw,
        )

    matched_date = re.fullmatch(ABSOLUTE_DATE_PATTERN, text)
    if matched_date:
        parsed_date = _date_from_parts(matched_date.groups())
        if parsed_date is None:
            return _unsupported(raw, timezone)
        return _absolute_range(
            parsed_date,
            parsed_date + timedelta(days=1),
            timezone,
            raw,
        )

    matched_month = re.fullmatch(r"(\d{4})年(\d{1,2})月", text)
    if matched_month:
        year = int(matched_month.group(1))
        month = int(matched_month.group(2))
        if not 1 <= month <= 12:
            return _unsupported(raw, timezone)
        start = date(year, month, 1)
        return _absolute_range(start, shift_months(start, 1), timezone, raw)

    fiscal_range = _resolve_fiscal_range(text, raw, temporal_context)
    if fiscal_range is not None:
        return fiscal_range

    matched_recent = re.fullmatch(r"(?:最近|近)(\d+)(天|日|周|个月|月|年)", text)
    if matched_recent:
        amount = int(matched_recent.group(1))
        if amount <= 0:
            return _unsupported(raw, timezone)
        unit = matched_recent.group(2)
        if unit in {"天", "日"}:
            start = reference_date - timedelta(days=amount - 1)
        elif unit == "周":
            start = reference_date - timedelta(days=amount * 7 - 1)
        elif unit in {"个月", "月"}:
            start = shift_months(reference_date, -amount) + timedelta(days=1)
        else:
            start = shift_months(reference_date, -12 * amount) + timedelta(days=1)
        return _absolute_range(
            start,
            reference_date + timedelta(days=1),
            timezone,
            raw,
        )

    periods = {
        "本周": ("week", False),
        "本月": ("month", False),
        "这个月": ("month", False),
        "本季度": ("quarter", False),
        "今年": ("year", False),
        "本年": ("year", False),
        "上周": ("week", True),
        "上月": ("month", True),
        "上个月": ("month", True),
        "上季度": ("quarter", True),
        "去年": ("year", True),
    }
    period = periods.get(text)
    if period is not None:
        unit, previous = period
        start, end_exclusive = period_bounds(
            reference_date,
            unit,
            temporal_context.week_start,
        )
        if previous:
            start, end_exclusive = period_bounds(
                start - timedelta(days=1),
                unit,
                temporal_context.week_start,
            )
        return _absolute_range(start, end_exclusive, timezone, raw)
    return _unsupported(raw, timezone)


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
    """兼容无 Run 上下文的旧投影；有上下文时只返回固定绝对范围。"""

    if temporal_context is not None:
        return resolve_time_range(raw, temporal_context)
    text = normalize_time_text(raw)
    if not text:
        return None
    lowered = text.lower()
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
    if lowered in single_dates:
        return {
            "kind": "single_date",
            "anchor": "today",
            "offset_days": single_dates[lowered],
            "timezone": timezone,
        }

    absolute_range = re.fullmatch(
        rf"{ABSOLUTE_DATE_PATTERN}(?:至|到|~|～|—|–){ABSOLUTE_DATE_PATTERN}",
        text,
    )
    if absolute_range:
        parts = absolute_range.groups()
        start = _date_from_parts(parts[:3])
        end = _date_from_parts(parts[3:])
        if start is None or end is None or end < start:
            return _unsupported(raw, timezone)
        return {
            "kind": "absolute_range",
            "start": start.isoformat(),
            "end_exclusive": (end + timedelta(days=1)).isoformat(),
            "timezone": timezone,
        }

    absolute_date = re.fullmatch(ABSOLUTE_DATE_PATTERN, text)
    if absolute_date:
        start = _date_from_parts(absolute_date.groups())
        if start is None:
            return _unsupported(raw, timezone)
        return {
            "kind": "absolute_range",
            "start": start.isoformat(),
            "end_exclusive": (start + timedelta(days=1)).isoformat(),
            "timezone": timezone,
        }

    absolute_month = re.fullmatch(r"(\d{4})年(\d{1,2})月", text)
    if absolute_month:
        year = int(absolute_month.group(1))
        month = int(absolute_month.group(2))
        if not 1 <= month <= 12:
            return _unsupported(raw, timezone)
        start = date(year, month, 1)
        return {
            "kind": "absolute_range",
            "start": start.isoformat(),
            "end_exclusive": shift_months(start, 1).isoformat(),
            "timezone": timezone,
        }

    named_fiscal_period = _match_named_fiscal_period(text)
    if named_fiscal_period is not None:
        fiscal_year, fiscal_quarter = named_fiscal_period
        if fiscal_quarter is None:
            return {
                "kind": "named_fiscal_period",
                "unit": "year",
                "fiscal_year": fiscal_year,
                "timezone": timezone,
            }
        return {
            "kind": "named_fiscal_period",
            "unit": "quarter",
            "fiscal_year": fiscal_year,
            "fiscal_quarter": fiscal_quarter,
            "timezone": timezone,
        }

    if text in _CURRENT_FISCAL_PERIODS:
        return {
            "kind": "current_fiscal_period",
            "unit": _CURRENT_FISCAL_PERIODS[text],
            "timezone": timezone,
        }
    if text in _PREVIOUS_FISCAL_PERIODS:
        return {
            "kind": "previous_fiscal_period",
            "unit": _PREVIOUS_FISCAL_PERIODS[text],
            "timezone": timezone,
        }

    matched = re.match(r"(最近|近)(\d+)(天|日|周|个月|月|年)", text)
    if matched:
        unit = {
            "天": "day",
            "日": "day",
            "周": "week",
            "个月": "month",
            "月": "month",
            "年": "year",
        }[matched.group(3)]
        return {
            "kind": "relative_range",
            "unit": unit,
            "amount": int(matched.group(2)),
            "anchor": "today",
            "include_current": True,
            "timezone": timezone,
        }

    current_periods = {
        "本周": "week",
        "本月": "month",
        "这个月": "month",
        "本季度": "quarter",
        "今年": "year",
        "本年": "year",
    }
    if text in current_periods:
        return {
            "kind": "current_period",
            "unit": current_periods[text],
            "timezone": timezone,
        }

    previous_periods = {
        "上周": "week",
        "上月": "month",
        "上个月": "month",
        "上季度": "quarter",
        "去年": "year",
    }
    if text in previous_periods:
        return {
            "kind": "previous_period",
            "unit": previous_periods[text],
            "timezone": timezone,
        }
    return _unsupported(raw, timezone)


def normalize_time_range_payload(
    time_range: dict[str, Any],
    *,
    temporal_context: TemporalContext | None = None,
) -> dict[str, Any]:
    """旧规则兼容入口；发生原文解析时必须记录 legacy_rule 来源。"""

    if temporal_context is not None:
        resolved = resolve_time_range_payload(time_range, temporal_context)
        if str(resolved.get("value_status") or "").lower() != "provided":
            return resolved
        return {
            **resolved,
            "interpretation_source": (
                time_range.get("interpretation_source") or "legacy_rule"
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
            time_range.get("interpretation_source") or "legacy_rule"
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


def _resolve_fiscal_range(
    text: str,
    raw: Any,
    temporal_context: TemporalContext,
) -> dict[str, Any] | None:
    named_period = _match_named_fiscal_period(text)
    fiscal_year: int
    fiscal_quarter: int | None
    if named_period is not None:
        fiscal_year, fiscal_quarter = named_period
        try:
            if fiscal_quarter is None:
                start, end_exclusive = named_fiscal_year_bounds(
                    fiscal_year,
                    temporal_context.fiscal_year_start_month,
                    temporal_context.fiscal_year_label,
                )
            else:
                start, end_exclusive = named_fiscal_quarter_bounds(
                    fiscal_year,
                    fiscal_quarter,
                    temporal_context.fiscal_year_start_month,
                    temporal_context.fiscal_year_label,
                )
        except (ValueError, OverflowError):
            return _unsupported(raw, temporal_context.timezone)
    else:
        unit = _CURRENT_FISCAL_PERIODS.get(text)
        previous = False
        if unit is None:
            unit = _PREVIOUS_FISCAL_PERIODS.get(text)
            previous = unit is not None
        if unit is None:
            return None
        bounds = fiscal_year_bounds if unit == "year" else fiscal_quarter_bounds
        start, end_exclusive = bounds(
            temporal_context.reference_date,
            temporal_context.fiscal_year_start_month,
        )
        if previous:
            start, end_exclusive = bounds(
                start - timedelta(days=1),
                temporal_context.fiscal_year_start_month,
            )
        fiscal_year, resolved_quarter = fiscal_period_label(
            start,
            temporal_context.fiscal_year_start_month,
            temporal_context.fiscal_year_label,
        )
        fiscal_quarter = resolved_quarter if unit == "quarter" else None

    metadata: dict[str, Any] = {
        "calendar": "fiscal",
        "fiscal_year": fiscal_year,
        "fiscal_year_start_month": temporal_context.fiscal_year_start_month,
        "fiscal_year_label": temporal_context.fiscal_year_label,
    }
    if fiscal_quarter is not None:
        metadata["fiscal_quarter"] = fiscal_quarter
    return _absolute_range(
        start,
        end_exclusive,
        temporal_context.timezone,
        raw,
        metadata=metadata,
    )


def _match_named_fiscal_period(text: str) -> tuple[int, int | None] | None:
    matched = re.fullmatch(
        (
            r"(?:(\d{4})(?:财年|财政年度)|FY(\d{4}))"
            r"(?:(?:第?([1-4])(?:季度|季))|(?:Q([1-4])))?"
        ),
        text,
        re.IGNORECASE,
    )
    if matched is None:
        return None
    fiscal_year = int(matched.group(1) or matched.group(2))
    quarter_text = matched.group(3) or matched.group(4)
    return fiscal_year, int(quarter_text) if quarter_text is not None else None


def _unsupported(raw: Any, timezone: str) -> dict[str, Any]:
    return {
        "kind": "unsupported",
        "raw": raw,
        "timezone": timezone,
    }


def _date_from_parts(parts: tuple[str, ...]) -> date | None:
    try:
        return date(*(int(part) for part in parts))
    except ValueError:
        return None


__all__ = [
    "normalize_time_range",
    "normalize_time_range_payload",
    "project_time_range_payload",
    "resolve_temporal_plan",
    "resolve_time_range",
    "resolve_time_range_payload",
]
