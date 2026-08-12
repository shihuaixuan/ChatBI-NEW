"""SQL 编译前统一校验 semantic binding 决策白名单。"""

from __future__ import annotations

from apps.retrieval.errors import RetrievalPermissionError, RetrievalQueryError
from apps.retrieval.models.dto import (
    ExecutableAssetReference,
    RetrievalDecision,
    RetrievalDecisionStatus,
    RetrievalResourceType,
)

EXECUTABLE_DECISION_STATUSES = frozenset(
    {
        RetrievalDecisionStatus.RESOLVED,
        RetrievalDecisionStatus.CROSS_MODEL,
        RetrievalDecisionStatus.DEGRADED,
    }
)


def is_compilation_decision_executable(
    decision_status: RetrievalDecisionStatus | str | None,
) -> bool:
    """判断检索决策是否可以在资产白名单约束下进入 SQL 编译。"""

    if decision_status is None:
        return False
    try:
        normalized = RetrievalDecisionStatus(decision_status)
    except ValueError:
        return False
    return normalized in EXECUTABLE_DECISION_STATUSES


def validate_compilation_assets(
    decision: RetrievalDecision,
    *,
    metric_ids: list[int],
    dimension_ids: list[int],
) -> tuple[ExecutableAssetReference, ...]:
    """拒绝未完成决策或不在统一白名单中的 SQL 资产。"""

    return validate_compilation_allowlist(
        decision.status,
        decision.allowed_asset_ids,
        metric_ids=metric_ids,
        dimension_ids=dimension_ids,
    )


def validate_compilation_allowlist(
    decision_status: RetrievalDecisionStatus,
    allowed_assets: list[ExecutableAssetReference]
    | tuple[ExecutableAssetReference, ...],
    *,
    metric_ids: list[int],
    dimension_ids: list[int],
) -> tuple[ExecutableAssetReference, ...]:
    """校验服务端保存的检索决策状态和资产白名单。"""

    if not is_compilation_decision_executable(decision_status):
        raise RetrievalQueryError(
            "SEMANTIC_BINDING_DECISION_NOT_EXECUTABLE",
            details={
                "reason_code": "SEMANTIC_BINDING_DECISION_NOT_EXECUTABLE",
                "decision_status": decision_status.value,
            },
        )

    requested = {(RetrievalResourceType.METRIC, asset_id) for asset_id in metric_ids}
    requested.update(
        (RetrievalResourceType.DIMENSION, asset_id) for asset_id in dimension_ids
    )
    allowed = {(item.asset_type, item.asset_id) for item in allowed_assets}
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
        item for item in allowed_assets if (item.asset_type, item.asset_id) in requested
    )


__all__ = [
    "EXECUTABLE_DECISION_STATUSES",
    "is_compilation_decision_executable",
    "validate_compilation_allowlist",
    "validate_compilation_assets",
]
