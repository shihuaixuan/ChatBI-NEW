"""输出数据集已发布语义契约允许的分析动作。"""

from apps.semantic.models.dto import (
    AnalysisOperationRejection,
    DatasetSchema,
    MetricAnalysisCapabilities,
)


def build_analysis_capabilities(
    schema: DatasetSchema,
) -> list[MetricAnalysisCapabilities]:
    """根据已发布 DatasetSchema 返回动作白名单和明确拒绝原因。"""

    result: list[MetricAnalysisCapabilities] = []
    for metric in schema.metrics:
        metric_ref = f"METRIC:{metric.id}:{metric.model}"
        metric_capabilities = [
            item
            for item in schema.metric_dimension_capabilities
            if isinstance(item, dict) and item.get("metric_id") == metric.id
        ]
        metric_contract = next(
            (
                item
                for item in schema.metric_contracts
                if isinstance(item, dict) and item.get("metric_id") == metric.id
            ),
            {},
        )
        available: list[str] = []
        rejected: list[AnalysisOperationRejection] = []
        if any(
            "GROUP_BY" in {str(value).upper() for value in item.get("usages") or []}
            and str(item.get("aggregation_safety") or "").upper() != "FORBIDDEN"
            for item in metric_capabilities
        ):
            available.append("breakdown")
        else:
            rejected.append(
                AnalysisOperationRejection(
                    operation="breakdown",
                    reason="METRIC_DIMENSION_GROUP_BY_NOT_CERTIFIED",
                )
            )
        metric_dimension_ids = {
            item.get("logical_dimension_id")
            for item in metric_capabilities
            if "GROUP_BY" in {str(value).upper() for value in item.get("usages") or []}
            and str(item.get("aggregation_safety") or "").upper() != "FORBIDDEN"
        }
        has_drilldown = any(
            str(metric.model) == model_id
            and refs
            and all(
                level.logical_dimension_id in metric_dimension_ids
                for level in hierarchy.levels
            )
            for hierarchy in schema.dimension_hierarchies
            for model_id, refs in hierarchy.dimension_refs_by_model.items()
        )
        if has_drilldown:
            available.append("drilldown")
        else:
            rejected.append(
                AnalysisOperationRejection(
                    operation="drilldown",
                    reason="DIMENSION_HIERARCHY_NOT_CERTIFIED",
                )
            )
        if any(
            relationship.target_metric_ref == metric_ref
            for relationship in schema.research_relationships
        ):
            available.append("validate_hypothesis")
        else:
            rejected.append(
                AnalysisOperationRejection(
                    operation="validate_hypothesis",
                    reason="METRIC_RELATIONSHIP_NOT_CERTIFIED",
                )
            )
        contribution_items = [
            item
            for item in metric_capabilities
            if "CONTRIBUTION"
            in {str(value).upper() for value in item.get("usages") or []}
        ]
        contribution_capability = any(
            str(item.get("aggregation_safety") or "").upper() != "FORBIDDEN"
            for item in contribution_items
        )
        if str(metric_contract.get("additivity") or "").upper() == "FULL" and contribution_capability:
            available.append("contribution")
        elif any(
            str(item.get("aggregation_safety") or "").upper() == "FORBIDDEN"
            for item in contribution_items
        ):
            rejected.append(
                AnalysisOperationRejection(
                    operation="contribution",
                    reason="CONTRIBUTION_AGGREGATION_FORBIDDEN",
                )
            )
        elif not contribution_items:
            rejected.append(
                AnalysisOperationRejection(
                    operation="contribution",
                    reason="CONTRIBUTION_NOT_CERTIFIED",
                )
            )
        else:
            rejected.append(
                AnalysisOperationRejection(
                    operation="contribution",
                    reason="METRIC_NOT_FULLY_ADDITIVE",
                )
            )
        result.append(
            MetricAnalysisCapabilities(
                metric_id=metric.id,
                available_operations=available,
                rejected_operations=rejected,
            )
        )
    return result


__all__ = ["build_analysis_capabilities"]
