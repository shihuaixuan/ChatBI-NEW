from __future__ import annotations

import re
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
    "上月",
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
)


def is_time_expression(value: Any) -> bool:
    """判断普通维度值是否实际是时间表达。"""

    text = _normalize_text(value)
    if not text:
        return False
    if text in {_normalize_text(keyword) for keyword in _TIME_KEYWORDS}:
        return True
    return re.fullmatch(r"(最近|近)\d+(天|日|周|个月|月|年)", text) is not None


def normalize_time_range(raw: Any, timezone: str = "Asia/Shanghai") -> dict[str, Any] | None:
    """把常见中文时间范围归一为 SQL 层可消费的受控结构。"""

    text = _normalize_text(raw)
    if not text:
        return None
    single_dates = {
        "今天": 0,
        "今日": 0,
        "昨天": -1,
        "昨日": -1,
        "明天": 1,
    }
    if text in single_dates:
        return {
            "kind": "single_date",
            "anchor": "today",
            "offset_days": single_dates[text],
            "timezone": timezone,
        }

    matched = re.fullmatch(r"(最近|近)(\d+)(天|日|周|个月|月|年)", text)
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
        "本季度": "quarter",
        "今年": "year",
        "本年": "year",
    }
    if text in current_periods:
        return {"kind": "current_period", "unit": current_periods[text], "timezone": timezone}

    previous_periods = {
        "上周": "week",
        "上月": "month",
        "上季度": "quarter",
        "去年": "year",
    }
    if text in previous_periods:
        return {"kind": "previous_period", "unit": previous_periods[text], "timezone": timezone}

    return {"kind": "unsupported", "raw": raw, "timezone": timezone}


def normalize_time_range_payload(time_range: dict[str, Any]) -> dict[str, Any]:
    """补齐 time_range.normalized，保留原始 raw 供解释与审计使用。"""

    if str(time_range.get("value_status") or "").lower() != "provided":
        return time_range
    if isinstance(time_range.get("normalized"), dict):
        return time_range
    normalized = normalize_time_range(time_range.get("raw"))
    if normalized is None:
        return time_range
    return {**time_range, "normalized": normalized}


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())
