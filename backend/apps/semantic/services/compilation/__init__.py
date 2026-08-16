"""P1-6 指标表达力的确定性编译辅助模块。"""

from apps.semantic.services.compilation.metric_expansion import (
    MetricExpansionError,
    build_ratio_expression,
    expand_metric_expression,
)
from apps.semantic.services.compilation.preaggregation import (
    PreAggregationError,
    render_preaggregation_subquery,
)
from apps.semantic.services.compilation.snapshot import (
    SnapshotAggregationError,
    render_snapshot_aggregation,
)
from apps.semantic.services.compilation.time_offset import (
    TimeOffsetDecision,
    TimeOffsetError,
    decide_time_offset,
    render_time_offset_expression,
)

__all__ = [
    "MetricExpansionError",
    "PreAggregationError",
    "SnapshotAggregationError",
    "TimeOffsetDecision",
    "TimeOffsetError",
    "build_ratio_expression",
    "decide_time_offset",
    "expand_metric_expression",
    "render_preaggregation_subquery",
    "render_snapshot_aggregation",
    "render_time_offset_expression",
]
