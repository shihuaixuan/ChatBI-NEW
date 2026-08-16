"""把可信查询形态绑定为时间分桶计划。"""

from __future__ import annotations

from typing import Any

_TIME_GRAINS = {"day", "week", "month", "quarter", "year"}


def derive_time_bucket(
    query_shape: dict[str, Any] | None,
    time_dimension_ids: list[int] | tuple[int, ...],
) -> dict[str, Any] | None:
    """根据查询形态和已绑定时间维度确定性生成时间分桶。"""

    shape = query_shape if isinstance(query_shape, dict) else {}
    grain = str(shape.get("time_grain") or "").strip().lower()
    if grain not in _TIME_GRAINS:
        return None
    dimension_id = next(
        (
            item
            for item in time_dimension_ids
            if isinstance(item, int) and not isinstance(item, bool) and item > 0
        ),
        None,
    )
    if dimension_id is None:
        return None
    return {"dimension_id": dimension_id, "grain": grain}


def derive_time_buckets(
    query_shape: dict[str, Any] | None,
    time_dimension_ids: list[int] | tuple[int, ...],
    time_ranges: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> list[dict[str, Any]]:
    """为多时段复用同一个时间维槽，仅附加区间序号。"""

    bucket = derive_time_bucket(query_shape, time_dimension_ids)
    if bucket is None:
        return []
    range_count = len(time_ranges) or 1
    return [
        {**bucket, "range_index": index}
        for index in range(range_count)
    ]


__all__ = ["derive_time_bucket", "derive_time_buckets"]
