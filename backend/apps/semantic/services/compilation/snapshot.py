"""快照指标的期初、期末及统计聚合表达式。"""

from __future__ import annotations


class SnapshotAggregationError(ValueError):
    """快照聚合策略不受支持。"""


def render_snapshot_aggregation(
    value_expression: str,
    time_expression: str,
    strategy: str,
) -> str:
    """生成快照值的确定性窗口表达式。"""

    value = value_expression.strip()
    time = time_expression.strip()
    normalized = strategy.strip().upper()
    if not value or not time:
        raise SnapshotAggregationError("SNAPSHOT_EXPRESSION_REQUIRED")
    if normalized == "ENDING":
        return f"FIRST_VALUE({value}) OVER (ORDER BY {time} DESC)"
    if normalized == "BEGINNING":
        return f"FIRST_VALUE({value}) OVER (ORDER BY {time} ASC)"
    if normalized in {"AVG", "MAX", "MIN"}:
        return f"{normalized}({value})"
    raise SnapshotAggregationError("SNAPSHOT_AGGREGATION_UNSUPPORTED")


__all__ = ["SnapshotAggregationError", "render_snapshot_aggregation"]
