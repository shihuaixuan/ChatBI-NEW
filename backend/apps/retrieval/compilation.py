"""SQL 编译前统一校验 semantic binding 决策白名单。"""

from __future__ import annotations

from apps.retrieval.errors import RetrievalPermissionError, RetrievalQueryError
from apps.retrieval.models.dto import (
    ExecutableAssetReference,
    RetrievalDecision,
    RetrievalDecisionStatus,
    RetrievalResourceType,
)


def validate_compilation_assets(
    decision: RetrievalDecision,
    *,
    metric_ids: list[int],
    dimension_ids: list[int],
) -> tuple[ExecutableAssetReference, ...]:
    """拒绝未完成决策或不在统一白名单中的 SQL 资产。"""

    executable_statuses = {
        RetrievalDecisionStatus.RESOLVED,
        RetrievalDecisionStatus.CROSS_MODEL,
    }
    if decision.status not in executable_statuses:
        raise RetrievalQueryError(
            "SEMANTIC_BINDING_DECISION_NOT_EXECUTABLE",
            details={
                "reason_code": "SEMANTIC_BINDING_DECISION_NOT_EXECUTABLE",
                "decision_status": decision.status.value,
            },
        )

    requested = {(RetrievalResourceType.METRIC, asset_id) for asset_id in metric_ids}
    requested.update(
        (RetrievalResourceType.DIMENSION, asset_id) for asset_id in dimension_ids
    )
    allowed = {(item.asset_type, item.asset_id) for item in decision.allowed_asset_ids}
    denied = sorted(
        requested - allowed,
        key=lambda item: (item[0].value, item[1]),
    )
    if denied:
        raise RetrievalPermissionError(
            "SQL 编译请求包含未由语义绑定决策放行的资产",
            details={
                "reason_code": "ASSET_NOT_IN_SEMANTIC_BINDING_ALLOWLIST",
                "denied_assets": [
                    {"asset_type": asset_type.value, "asset_id": asset_id}
                    for asset_type, asset_id in denied
                ],
            },
        )
    return tuple(
        item
        for item in decision.allowed_asset_ids
        if (item.asset_type, item.asset_id) in requested
    )


__all__ = ["validate_compilation_assets"]
