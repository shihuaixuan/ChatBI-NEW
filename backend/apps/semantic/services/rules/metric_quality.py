from typing import Protocol

from apps.semantic.repository.metric_repository import MetricDependencyFacts


class MetricQualityTarget(Protocol):
    """指标质量校验所需的最小状态。"""

    expr: str | None
    fields: list[str]
    define_type: str
    metric_refs: list[int]
    quality_status: str | None
    quality_message: str | None


def validate_metric_quality(metric: MetricQualityTarget) -> None:
    """根据指标定义完整性更新质量状态。"""
    if not metric.expr and not metric.fields:
        metric.quality_status = "INVALID"
        metric.quality_message = "指标表达式或依赖字段不能为空"
        return
    if metric.define_type == "METRIC" and not metric.metric_refs:
        metric.quality_status = "INVALID"
        metric.quality_message = "派生指标必须引用至少一个指标"
        return
    metric.quality_status = "VALID"
    metric.quality_message = ""


def validate_metric_dependencies(
    metric: MetricQualityTarget,
    facts: MetricDependencyFacts,
) -> None:
    """根据模型字段和指标引用事实更新指标质量。"""

    if metric.quality_status == "INVALID":
        return
    # 派生指标的 fields 保存的是指标业务标识，不是物理字段；其有效性由 metric_refs 校验。
    if metric.define_type != "METRIC" and facts.known_fields and metric.fields:
        missing_fields = [
            field for field in metric.fields if field not in facts.known_fields
        ]
        if missing_fields:
            metric.quality_status = "INVALID"
            metric.quality_message = f"指标依赖字段不存在: {', '.join(missing_fields)}"
            return
    if metric.metric_refs:
        missing_refs = [
            ref for ref in metric.metric_refs if ref not in facts.existing_metric_ids
        ]
        if missing_refs:
            metric.quality_status = "INVALID"
            metric.quality_message = (
                f"派生指标引用不存在: {', '.join(str(item) for item in missing_refs)}"
            )
