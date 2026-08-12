"""自然日历边界与月份平移规则。"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from apps.temporal.models import WeekStart

_WEEKDAY_INDEX: dict[WeekStart, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def shift_months(anchor: date, months: int) -> date:
    """按自然月平移日期，目标月份天数不足时收敛到月末。"""

    total = anchor.year * 12 + anchor.month - 1 + months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def period_bounds(
    anchor: date,
    unit: str,
    week_start: WeekStart = "monday",
) -> tuple[date, date]:
    """返回自然周期的左闭右开日期边界。"""

    if unit == "week":
        start_index = _WEEKDAY_INDEX[week_start]
        elapsed_days = (anchor.weekday() - start_index) % 7
        start = anchor - timedelta(days=elapsed_days)
        return start, start + timedelta(days=7)
    if unit == "month":
        start = anchor.replace(day=1)
        return start, shift_months(start, 1)
    if unit == "quarter":
        start = date(anchor.year, 3 * ((anchor.month - 1) // 3) + 1, 1)
        return start, shift_months(start, 3)
    if unit == "year":
        return date(anchor.year, 1, 1), date(anchor.year + 1, 1, 1)
    raise ValueError("TEMPORAL_PERIOD_UNIT_UNSUPPORTED")


def fiscal_year_bounds(
    anchor: date,
    fiscal_year_start_month: int,
) -> tuple[date, date]:
    """返回 anchor 所属财政年度的左闭右开边界。"""

    _validate_fiscal_year_start_month(fiscal_year_start_month)
    start_year = (
        anchor.year if anchor.month >= fiscal_year_start_month else anchor.year - 1
    )
    start = date(start_year, fiscal_year_start_month, 1)
    return start, shift_months(start, 12)


def fiscal_quarter_bounds(
    anchor: date,
    fiscal_year_start_month: int,
) -> tuple[date, date]:
    """返回 anchor 所属财政季度的左闭右开边界。"""

    fiscal_start, _ = fiscal_year_bounds(anchor, fiscal_year_start_month)
    elapsed_months = (
        (anchor.year - fiscal_start.year) * 12 + anchor.month - fiscal_start.month
    )
    start = shift_months(fiscal_start, (elapsed_months // 3) * 3)
    return start, shift_months(start, 3)


def named_fiscal_year_bounds(
    fiscal_year: int,
    fiscal_year_start_month: int,
    fiscal_year_label: str,
) -> tuple[date, date]:
    """按企业采用的起始年或结束年命名规则解析指定财年。"""

    _validate_fiscal_year_start_month(fiscal_year_start_month)
    if fiscal_year_label not in {"start_year", "end_year"}:
        raise ValueError("TEMPORAL_FISCAL_YEAR_LABEL_UNSUPPORTED")
    start_year = fiscal_year
    if fiscal_year_label == "end_year" and fiscal_year_start_month != 1:
        start_year -= 1
    start = date(start_year, fiscal_year_start_month, 1)
    return start, shift_months(start, 12)


def named_fiscal_quarter_bounds(
    fiscal_year: int,
    quarter: int,
    fiscal_year_start_month: int,
    fiscal_year_label: str,
) -> tuple[date, date]:
    """返回指定财年和财季的绝对边界。"""

    if quarter not in {1, 2, 3, 4}:
        raise ValueError("TEMPORAL_FISCAL_QUARTER_INVALID")
    fiscal_start, _ = named_fiscal_year_bounds(
        fiscal_year,
        fiscal_year_start_month,
        fiscal_year_label,
    )
    start = shift_months(fiscal_start, (quarter - 1) * 3)
    return start, shift_months(start, 3)


def fiscal_period_label(
    anchor: date,
    fiscal_year_start_month: int,
    fiscal_year_label: str,
) -> tuple[int, int]:
    """返回 anchor 所属财年的展示年份和季度序号。"""

    fiscal_start, _ = fiscal_year_bounds(anchor, fiscal_year_start_month)
    if fiscal_year_label == "start_year" or fiscal_year_start_month == 1:
        fiscal_year = fiscal_start.year
    elif fiscal_year_label == "end_year":
        fiscal_year = fiscal_start.year + 1
    else:
        raise ValueError("TEMPORAL_FISCAL_YEAR_LABEL_UNSUPPORTED")
    elapsed_months = (
        (anchor.year - fiscal_start.year) * 12 + anchor.month - fiscal_start.month
    )
    return fiscal_year, elapsed_months // 3 + 1


def _validate_fiscal_year_start_month(value: int) -> None:
    if value < 1 or value > 12:
        raise ValueError("TEMPORAL_FISCAL_YEAR_START_MONTH_INVALID")


__all__ = [
    "fiscal_period_label",
    "fiscal_quarter_bounds",
    "fiscal_year_bounds",
    "named_fiscal_quarter_bounds",
    "named_fiscal_year_bounds",
    "period_bounds",
    "shift_months",
]
