"""JioNLP 时间解析结果到项目时间结构的适配。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import jionlp as jio

from apps.temporal.models import TemporalContext

_DAY_START = time.min
_DAY_END = time(23, 59, 59)
_GRAIN_SUFFIXES = ("每天", "每日", "按天", "按日", "按周", "按月", "按季度", "按年")
_UNSUPPORTED_FISCAL_MARKERS = ("财年", "财季")


def parse_jionlp_time(
    raw: Any,
    temporal_context: TemporalContext,
) -> dict[str, Any] | None:
    """使用固定 Run 基准时间调用 JioNLP，并返回项目的绝对范围结构。"""

    text = _normalize_text(raw)
    if not text:
        return None
    if any(marker in text for marker in _UNSUPPORTED_FISCAL_MARKERS):
        return _unsupported(raw, temporal_context)
    parse_text = _strip_grain_suffix(text)
    try:
        parsed = jio.parse_time(
            parse_text,
            time_base=temporal_context.reference_at,
        )
    except (TypeError, ValueError):
        return _unsupported(raw, temporal_context)

    if not isinstance(parsed, dict):
        return _unsupported(raw, temporal_context)
    if parsed.get("type") not in {"time_point", "time_span"}:
        return _unsupported(raw, temporal_context)
    bounds = parsed.get("time")
    if not isinstance(bounds, list) or len(bounds) != 2:
        return _unsupported(raw, temporal_context)

    start = _parse_datetime(bounds[0])
    end = _parse_datetime(bounds[1])
    if start is None or end is None or end < start:
        return _unsupported(raw, temporal_context)
    date_range = _to_date_range(start, end)
    if date_range is None:
        return _unsupported(raw, temporal_context)
    start_date, end_exclusive = date_range
    return {
        "kind": "absolute_range",
        "start": start_date.isoformat(),
        "end_exclusive": end_exclusive.isoformat(),
        "timezone": temporal_context.timezone,
        "source_raw": str(raw),
    }


def extract_jionlp_time_entities(value: Any) -> list[dict[str, Any]]:
    """调用 JioNLP 时间实体抽取，供时间表达识别使用。"""

    text = _normalize_text(value)
    if not text:
        return []
    try:
        entities = jio.ner.extract_time(text)
    except (TypeError, ValueError):
        return []
    return [item for item in entities if isinstance(item, dict)]


def _normalize_text(value: Any) -> str:
    return "".join(str(value or "").strip().split())


def _strip_grain_suffix(text: str) -> str:
    for suffix in _GRAIN_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_date_range(
    start: datetime,
    end: datetime,
) -> tuple[date, date] | None:
    """把 JioNLP 的时间边界转换为日期左闭右开范围。"""

    if start.time() == _DAY_START and end.time() == _DAY_END:
        return start.date(), end.date() + timedelta(days=1)
    if start.time() == _DAY_START and end.time() == _DAY_START:
        return start.date(), end.date()
    # JioNLP 对“最近 N 天”会返回相同的非零时刻，按日期查询时向上取整。
    if start.time() == end.time() and start.time() != _DAY_START:
        return start.date() + timedelta(days=1), end.date() + timedelta(days=1)
    # JioNLP 对“最近 N 个月”会返回起点零点和基准时刻，按日期查询时包含基准日。
    if start.time() == _DAY_START and end.time() != _DAY_END:
        return start.date(), end.date() + timedelta(days=1)
    return None


def _unsupported(
    raw: Any,
    temporal_context: TemporalContext,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "unsupported",
        "raw": raw,
        "timezone": temporal_context.timezone,
    }
    return result


__all__ = ["extract_jionlp_time_entities", "parse_jionlp_time"]
