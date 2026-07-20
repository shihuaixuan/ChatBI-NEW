"""ChatBI 时间表达识别与归一化的权威实现。"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

_TIME_KEYWORDS = (
    "今天",
    "今日",
    "昨天",
    "昨日",
    "明天",
    "本周",
    "上周",
    "本月",
    "这个月",
    "上月",
    "上个月",
    "本季度",
    "上季度",
    "今年",
    "本年",
    "去年",
    "按天",
    "按周",
    "按月",
    "按季度",
    "按年",
    "today",
    "yesterday",
    "tomorrow",
)


def is_time_expression(value: Any) -> bool:
    """判断普通维度值是否实际是时间表达。"""

    text = _normalize_text(value)
    if not text:
        return False
    if text.lower() in {_normalize_text(keyword).lower() for keyword in _TIME_KEYWORDS}:
        return True
    if re.fullmatch(r"\d{4}年\d{1,2}月(?:\d{1,2}日)?", text):
        return True
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
        return True
    return re.fullmatch(r"(最近|近)\d+(天|日|周|个月|月|年)", text) is not None


def normalize_time_range(raw: Any, timezone: str = "Asia/Shanghai") -> dict[str, Any] | None:
    """把常见时间范围归一为 SQL 层可消费的受控结构。"""

    text = _normalize_text(raw)
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

    absolute_date = re.fullmatch(r"(\d{4})(?:年|-)(\d{1,2})(?:月|-)(\d{1,2})日?", text)
    if absolute_date:
        try:
            start = date(*(int(part) for part in absolute_date.groups()))
        except ValueError:
            return {"kind": "unsupported", "raw": raw, "timezone": timezone}
        return {
            "kind": "absolute_range",
            "start": start.isoformat(),
            "end_exclusive": (start + timedelta(days=1)).isoformat(),
            "timezone": timezone,
        }

    # 绝对月份统一转换为左闭右开区间，避免月底天数差异。
    absolute_month = re.fullmatch(r"(\d{4})年(\d{1,2})月", text)
    if absolute_month:
        year = int(absolute_month.group(1))
        month = int(absolute_month.group(2))
        if not 1 <= month <= 12:
            return {"kind": "unsupported", "raw": raw, "timezone": timezone}
        end_year = year + (1 if month == 12 else 0)
        end_month = 1 if month == 12 else month + 1
        return {
            "kind": "absolute_range",
            "start": f"{year:04d}-{month:02d}-01",
            "end_exclusive": f"{end_year:04d}-{end_month:02d}-01",
            "timezone": timezone,
        }

    # 模型可能把“每天/按天”等粒度词一并放入 raw，只消费开头的范围部分。
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
        return {"kind": "current_period", "unit": current_periods[text], "timezone": timezone}

    previous_periods = {
        "上周": "week",
        "上月": "month",
        "上个月": "month",
        "上季度": "quarter",
        "去年": "year",
    }
    if text in previous_periods:
        return {"kind": "previous_period", "unit": previous_periods[text], "timezone": timezone}

    return {"kind": "unsupported", "raw": raw, "timezone": timezone}


def normalize_time_range_payload(time_range: dict[str, Any]) -> dict[str, Any]:
    """补齐 time_range.normalized，原始 raw 仅用于解释与审计。"""

    if str(time_range.get("value_status") or "").lower() != "provided":
        return time_range
    normalized = normalize_time_range(time_range.get("raw"))
    if normalized is None:
        return time_range
    return {**time_range, "normalized": normalized}


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


__all__ = ["is_time_expression", "normalize_time_range", "normalize_time_range_payload"]
