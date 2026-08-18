"""指标条件的 WHERE/HAVING 确定性裁决。"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

from apps.chatbi.models.dto.mention import MetricCondition


class MetricConditionStageError(ValueError):
    """条件目标无法确定聚合层级时抛出的稳定错误。"""


class ResolvedMetricCondition(BaseModel):
    """带服务端执行阶段的指标条件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    condition: MetricCondition
    stage: Literal["where", "having"]


def resolve_metric_condition_stage(
    condition: MetricCondition,
    *,
    aggregated_refs: Collection[str] = (),
    physical_refs: Collection[str] = (),
    aggregation_stage: Mapping[str, Literal["where", "having"]] | None = None,
) -> ResolvedMetricCondition:
    """按运行时事实确定条件阶段，禁止由模型直接指定。"""

    reference = condition.metric_ref
    if aggregation_stage is not None and reference in aggregation_stage:
        return ResolvedMetricCondition(
            condition=condition,
            stage=aggregation_stage[reference],
        )
    is_aggregated = reference in set(aggregated_refs)
    is_physical = reference in set(physical_refs)
    if is_aggregated and not is_physical:
        return ResolvedMetricCondition(condition=condition, stage="having")
    if is_physical and not is_aggregated:
        return ResolvedMetricCondition(condition=condition, stage="where")
    raise MetricConditionStageError("METRIC_CONDITION_STAGE_UNRESOLVED")


__all__ = [
    "MetricConditionStageError",
    "ResolvedMetricCondition",
    "resolve_metric_condition_stage",
]
