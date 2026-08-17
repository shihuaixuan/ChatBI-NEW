"""把可信绝对时间范围渲染为 SQL 条件。"""

from __future__ import annotations

import re
from datetime import date
from typing import Any


class TemporalSQLRenderError(ValueError):
    """时间结构不能安全渲染时抛出的明确错误。"""


def render_time_filter_condition(expr: str, value: Any) -> str | None:
    """仅渲染绝对左闭右开范围，非时间值返回空结果。"""

    if not isinstance(value, dict):
        return None
    # 兼容旧计划中的 {start, end}；内部统一成左闭右开范围后再渲染。
    if "kind" not in value and value.get("start") and value.get("end"):
        value = {**value, "kind": "absolute_range", "end_exclusive": value["end"]}
    if str(value.get("kind") or "").lower() != "absolute_range":
        raise TemporalSQLRenderError("TEMPORAL_SQL_TIME_RANGE_UNSUPPORTED")
    start = _validated_iso_date(value.get("start"))
    end_exclusive = _validated_iso_date(value.get("end_exclusive"))
    if start is None or end_exclusive is None or start >= end_exclusive:
        raise TemporalSQLRenderError("TEMPORAL_SQL_TIME_RANGE_INVALID")
    return f"{expr} >= '{start}' and {expr} < '{end_exclusive}'"


def _validated_iso_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


__all__ = ["TemporalSQLRenderError", "render_time_filter_condition"]
