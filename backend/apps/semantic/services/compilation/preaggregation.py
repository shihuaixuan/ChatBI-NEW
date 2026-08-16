"""PRE_AGGREGATE_REQUIRED 的受控预聚合子查询渲染。"""

from __future__ import annotations

import re


class PreAggregationError(ValueError):
    """预聚合计划缺少必要字段。"""


def render_preaggregation_subquery(
    source_sql: str,
    *,
    select_expressions: list[str] | tuple[str, ...],
    group_expressions: list[str] | tuple[str, ...],
    alias: str = "preagg",
) -> str:
    """将明细来源收敛为指定粒度，再交给外层查询继续 join/聚合。"""

    if not source_sql.strip() or not select_expressions or not group_expressions:
        raise PreAggregationError("PRE_AGGREGATION_EXPRESSIONS_REQUIRED")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
        raise PreAggregationError("PRE_AGGREGATION_ALIAS_INVALID")
    selected = ", ".join(select_expressions)
    grouped = ", ".join(group_expressions)
    return f"(select {selected} from ({source_sql}) source group by {grouped}) {alias}"


__all__ = ["PreAggregationError", "render_preaggregation_subquery"]
