"""同环比时间偏移的单 SQL 能力判定与窗口表达式渲染。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class TimeOffsetError(ValueError):
    """时间偏移参数不满足编译边界。"""


@dataclass(frozen=True, slots=True)
class TimeOffsetDecision:
    mode: Literal["single_sql", "dual_query"]
    periods: int
    method: Literal["yoy", "mom", "custom"]
    reason: str | None = None


def decide_time_offset(
    *,
    method: str,
    grain: str | None,
    range_count: int = 1,
) -> TimeOffsetDecision:
    """只对固定周期且单一时段使用窗口偏移，复杂范围降级双 QueryTask。"""

    normalized_method = str(method).strip().lower()
    if normalized_method not in {"yoy", "mom", "custom"}:
        raise TimeOffsetError("TIME_OFFSET_METHOD_UNSUPPORTED")
    if range_count <= 0:
        raise TimeOffsetError("TIME_OFFSET_RANGE_COUNT_INVALID")
    if normalized_method == "custom" or range_count != 1:
        return TimeOffsetDecision("dual_query", 0, normalized_method, "COMPLEX_TIME_OFFSET")
    if grain not in {"month", "quarter", "year"}:
        return TimeOffsetDecision("dual_query", 0, normalized_method, "TIME_GRAIN_NOT_FIXED")
    periods = 12 if normalized_method == "yoy" else 1
    if grain == "quarter" and normalized_method == "yoy":
        periods = 4
    if grain == "year" and normalized_method == "yoy":
        periods = 1
    return TimeOffsetDecision("single_sql", periods, normalized_method)


def render_time_offset_expression(
    expression: str,
    *,
    time_alias: str,
    periods: int,
    dialect: str | None = None,
) -> str:
    """渲染上一周期值；差异计算由上层 ComputeTask 负责。"""

    if not expression.strip() or not time_alias.strip() or periods <= 0:
        raise TimeOffsetError("TIME_OFFSET_ARGUMENT_INVALID")
    # LAG 是主流 SQL 方言共同支持的窗口函数；dialect 参数保留供后续转译。
    _ = dialect
    return f"LAG({expression}, {periods}) OVER (ORDER BY {time_alias})"


__all__ = [
    "TimeOffsetDecision",
    "TimeOffsetError",
    "decide_time_offset",
    "render_time_offset_expression",
]
