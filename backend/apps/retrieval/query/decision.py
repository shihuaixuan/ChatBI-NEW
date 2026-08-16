"""语义绑定澄清后的确定性决策收敛。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from apps.retrieval.errors import RetrievalQueryError
from apps.retrieval.models.dto import (
    AssetReference,
    ExecutableAssetReference,
    RetrievalBundle,
    RetrievalChannelStatus,
    RetrievalDecision,
    RetrievalDecisionStatus,
    RetrievalHit,
    RetrievalPurpose,
    RetrievalResourceType,
    RetrievalSlotDecision,
    SemanticClarificationBinding,
)


def apply_semantic_clarification(
    bundle: RetrievalBundle,
    bindings: list[SemanticClarificationBinding],
    *,
    required_subquery_ids: set[str] | None = None,
) -> RetrievalBundle:
    """把用户选择应用到原始候选，并重新计算决策状态与编译白名单。"""

    if not bindings:
        raise RetrievalQueryError(
            "语义澄清没有提供可验证的资产绑定",
            details={"reason_code": "SEMANTIC_CLARIFICATION_BINDINGS_REQUIRED"},
        )
    selected_by_slot = {item.subquery_id: item for item in bindings}
    if len(selected_by_slot) != len(bindings):
        raise RetrievalQueryError(
            "同一语义槽位不能选择多个资产",
            details={"reason_code": "SEMANTIC_CLARIFICATION_SLOT_DUPLICATED"},
        )

    slots_by_id = {item.subquery_id: item for item in bundle.decision.slot_decisions}
    resolved_assets: dict[str, AssetReference] = {}
    for subquery_id, binding in selected_by_slot.items():
        slot = slots_by_id.get(subquery_id)
        if slot is None:
            raise RetrievalQueryError(
                "语义澄清引用了不存在的槽位",
                details={
                    "reason_code": "SEMANTIC_CLARIFICATION_SLOT_NOT_FOUND",
                    "subquery_id": subquery_id,
                },
            )
        if slot.status != RetrievalDecisionStatus.AMBIGUOUS:
            raise RetrievalQueryError(
                "语义澄清只能应用到待澄清槽位",
                details={
                    "reason_code": "SEMANTIC_CLARIFICATION_SLOT_NOT_AMBIGUOUS",
                    "subquery_id": subquery_id,
                    "slot_status": slot.status.value,
                },
            )
        candidate = _selected_candidate(slot, binding)
        resolved_assets[subquery_id] = candidate

    updated_slots = [
        _resolved_slot(
            slot,
            resolved_assets[slot.subquery_id],
            reason_code="USER_CLARIFICATION_SELECTED",
        )
        if slot.subquery_id in resolved_assets
        else slot
        for slot in bundle.decision.slot_decisions
    ]
    compatible_dimensions = compatible_dimension_selections(
        bundle.bindings.all_hits(),
        updated_slots,
    )
    if compatible_dimensions:
        updated_slots = [
            _resolved_slot(
                slot,
                compatible_dimensions[slot.subquery_id],
                reason_code=(
                    "IDENTITY_DISAMBIGUATED_BY_METRIC_MODEL_COMPATIBILITY"
                ),
            )
            if slot.subquery_id in compatible_dimensions
            else slot
            for slot in updated_slots
        ]
    resolved_subquery_ids = {*resolved_assets, *compatible_dimensions}
    remaining_ambiguities = [
        item
        for item in bundle.decision.ambiguities
        if item.subquery_id not in resolved_subquery_ids
    ]
    status = _decision_status(
        bundle,
        updated_slots,
        required_subquery_ids=required_subquery_ids,
    )
    reason_codes = [
        f"SEMANTIC_BINDING_{status.value.upper()}",
        *(
            code
            for code in bundle.decision.reason_codes
            if not code.startswith("SEMANTIC_BINDING_")
        ),
        "SEMANTIC_BINDING_RESOLVED_BY_USER_CLARIFICATION",
    ]
    updated_decision = RetrievalDecision(
        status=status,
        slot_decisions=updated_slots,
        ambiguities=remaining_ambiguities,
        allowed_asset_ids=_allowed_executable_assets(updated_slots),
        reason_codes=list(dict.fromkeys(reason_codes)),
    )
    return bundle.model_copy(update={"decision": updated_decision})


def selection_requires_cross_model(
    hits: Iterable[RetrievalHit],
    selected_assets: Iterable[AssetReference],
) -> bool:
    """判断最终选中资产是否需要跨模型查询。"""

    selected_keys = {_asset_key(item) for item in selected_assets}
    selected_hits = [
        hit
        for hit in hits
        if hit.asset_ref is not None and _asset_key(hit.asset_ref) in selected_keys
    ]
    metric_hits = [
        hit for hit in selected_hits if hit.resource_type == RetrievalResourceType.METRIC
    ]
    metric_model_ids = {
        hit.asset_ref.model_id
        for hit in metric_hits
        if hit.asset_ref is not None and hit.asset_ref.model_id is not None
    }
    if len(metric_model_ids) > 1:
        return True
    if not metric_model_ids:
        executable_models = {
            hit.asset_ref.model_id
            for hit in selected_hits
            if hit.resource_type
            in {RetrievalResourceType.METRIC, RetrievalResourceType.DIMENSION}
            and hit.asset_ref is not None
            and hit.asset_ref.model_id is not None
        }
        return len(executable_models) > 1

    metric_model_id = next(iter(metric_model_ids))
    compatible_dimension_ids_by_metric = [
        {
            int(value)
            for value in hit.metadata.get("compatible_dimension_ids", [])
            if isinstance(value, int) and value > 0
        }
        for hit in metric_hits
    ]
    for hit in selected_hits:
        if hit.resource_type not in {
            RetrievalResourceType.DIMENSION,
            RetrievalResourceType.VALUE,
        }:
            continue
        assert hit.asset_ref is not None
        if hit.asset_ref.model_id == metric_model_id:
            continue
        if not all(
            hit.asset_ref.asset_id in compatible_ids
            for compatible_ids in compatible_dimension_ids_by_metric
        ):
            return True
    return False


def compatible_dimension_selections(
    hits: Iterable[RetrievalHit],
    decisions: list[RetrievalSlotDecision],
) -> dict[str, AssetReference]:
    """用已选指标的模型兼容关系确定唯一的维度身份候选。"""

    hit_list = list(hits)
    selected_metric_keys = {
        _asset_key(asset)
        for decision in decisions
        if decision.status == RetrievalDecisionStatus.RESOLVED
        for asset in decision.selected_assets
        if asset.asset_type == RetrievalResourceType.METRIC
    }
    metric_hits = [
        hit
        for hit in hit_list
        if hit.asset_ref is not None
        and hit.resource_type == RetrievalResourceType.METRIC
        and _asset_key(hit.asset_ref) in selected_metric_keys
    ]
    if not metric_hits:
        return {}

    result: dict[str, AssetReference] = {}
    for decision in decisions:
        if (
            decision.purpose != RetrievalPurpose.DIMENSION
            or decision.status != RetrievalDecisionStatus.AMBIGUOUS
        ):
            continue
        candidate_keys = {_asset_key(asset) for asset in decision.candidate_assets}
        compatible_hits = [
            hit
            for hit in hit_list
            if hit.asset_ref is not None
            and _asset_key(hit.asset_ref) in candidate_keys
            and (hit.scores.exact is not None or hit.scores.alias is not None)
            and _dimension_is_compatible_with_metrics(hit, metric_hits)
        ]
        unique_assets = _unique_hit_assets(compatible_hits)
        unique_assets = prefer_dimension_assets(
            hit_list,
            [hit.asset_ref for hit in metric_hits if hit.asset_ref is not None],
            unique_assets,
        )
        if len(unique_assets) == 1:
            result[decision.subquery_id] = unique_assets[0]
    return result


def prefer_dimension_assets(
    hits: Iterable[RetrievalHit],
    metric_assets: Iterable[AssetReference],
    dimension_assets: Iterable[AssetReference],
) -> list[AssetReference]:
    """在兼容候选中优先选择与指标同模型的维度。"""

    hit_list = list(hits)
    metric_list = list(metric_assets)
    unique_dimensions = _unique_assets(dimension_assets)
    if not metric_list:
        return unique_dimensions

    metric_hits = {
        _asset_key(hit.asset_ref): hit
        for hit in hit_list
        if hit.asset_ref is not None
        and hit.resource_type == RetrievalResourceType.METRIC
    }
    selected_metric_hits = [
        metric_hits[_asset_key(metric)]
        for metric in metric_list
        if _asset_key(metric) in metric_hits
    ]
    if len(selected_metric_hits) != len(metric_list):
        return []

    dimension_hits = {
        _asset_key(hit.asset_ref): hit
        for hit in hit_list
        if hit.asset_ref is not None
        and hit.resource_type == RetrievalResourceType.DIMENSION
    }
    compatible_dimensions = [
        dimension
        for dimension in unique_dimensions
        if (dimension_hit := dimension_hits.get(_asset_key(dimension))) is not None
        and _dimension_is_compatible_with_metrics(
            dimension_hit, selected_metric_hits
        )
    ]
    if not compatible_dimensions:
        return []

    # 只有存在同模型候选时才收窄，关联模型在没有同模型候选时继续保留。
    same_model_counts = [
        sum(
            1
            for metric in metric_list
            if metric.model_id is not None
            and metric.model_id == dimension.model_id
        )
        for dimension in compatible_dimensions
    ]
    max_same_model_count = max(same_model_counts)
    if max_same_model_count == 0:
        return compatible_dimensions
    return [
        dimension
        for dimension, same_model_count in zip(
            compatible_dimensions, same_model_counts, strict=True
        )
        if same_model_count == max_same_model_count
    ]


def _selected_candidate(
    slot: RetrievalSlotDecision,
    binding: SemanticClarificationBinding,
) -> AssetReference:
    candidates = [
        item
        for item in slot.candidate_assets
        if item.asset_type == binding.asset_type
        and item.asset_id == binding.asset_id
        and (binding.model_id is None or item.model_id == binding.model_id)
    ]
    if len(candidates) != 1:
        raise RetrievalQueryError(
            "语义澄清选择必须唯一匹配原始候选",
            details={
                "reason_code": "SEMANTIC_CLARIFICATION_CANDIDATE_INVALID",
                "subquery_id": binding.subquery_id,
                "asset_type": binding.asset_type.value,
                "asset_id": binding.asset_id,
                "model_id": binding.model_id,
            },
        )
    return candidates[0]


def _resolved_slot(
    slot: RetrievalSlotDecision,
    selected: AssetReference,
    *,
    reason_code: str,
) -> RetrievalSlotDecision:
    return slot.model_copy(
        update={
            "status": RetrievalDecisionStatus.RESOLVED,
            "selected_assets": [selected],
            "reason_codes": [reason_code],
        }
    )


def _dimension_is_compatible_with_metrics(
    dimension_hit: RetrievalHit,
    metric_hits: list[RetrievalHit],
) -> bool:
    assert dimension_hit.asset_ref is not None
    dimension = dimension_hit.asset_ref
    for metric_hit in metric_hits:
        assert metric_hit.asset_ref is not None
        metric = metric_hit.asset_ref
        if dimension.model_id is not None and dimension.model_id == metric.model_id:
            continue
        compatible_ids = {
            int(value)
            for value in metric_hit.metadata.get("compatible_dimension_ids", [])
            if isinstance(value, int) and value > 0
        }
        if dimension.asset_id not in compatible_ids:
            return False
    return True


def _unique_hit_assets(hits: list[RetrievalHit]) -> list[AssetReference]:
    result: list[AssetReference] = []
    seen: set[tuple[str, int, int | None]] = set()
    for hit in hits:
        assert hit.asset_ref is not None
        key = _asset_key(hit.asset_ref)
        if key in seen:
            continue
        seen.add(key)
        result.append(hit.asset_ref)
    return result


def _unique_assets(assets: Iterable[AssetReference]) -> list[AssetReference]:
    result: list[AssetReference] = []
    seen: set[tuple[str, int, int | None]] = set()
    for asset in assets:
        key = _asset_key(asset)
        if key in seen:
            continue
        seen.add(key)
        result.append(asset)
    return result


def _decision_status(
    bundle: RetrievalBundle,
    slots: list[RetrievalSlotDecision],
    *,
    required_subquery_ids: set[str] | None,
) -> RetrievalDecisionStatus:
    required = [
        item
        for item in slots
        if (
            item.subquery_id in required_subquery_ids
            if required_subquery_ids is not None
            else item.purpose != RetrievalPurpose.TERM
        )
    ]
    resolved = [
        item for item in required if item.status == RetrievalDecisionStatus.RESOLVED
    ]
    selected_assets = [asset for item in resolved for asset in item.selected_assets]
    if required and len(resolved) == len(required) and selection_requires_cross_model(
        bundle.bindings.all_hits(), selected_assets
    ):
        return RetrievalDecisionStatus.CROSS_MODEL
    if any(
        item.status in {
            RetrievalChannelStatus.UNAVAILABLE,
            RetrievalChannelStatus.FAILED,
        }
        for item in bundle.diagnostics.channels
    ):
        return RetrievalDecisionStatus.DEGRADED
    if any(item.status == RetrievalDecisionStatus.AMBIGUOUS for item in required):
        return RetrievalDecisionStatus.AMBIGUOUS
    if not required or not resolved:
        return RetrievalDecisionStatus.MISSED
    if len(resolved) != len(required):
        return RetrievalDecisionStatus.PARTIAL
    return RetrievalDecisionStatus.RESOLVED


def _allowed_executable_assets(
    decisions: list[RetrievalSlotDecision],
) -> list[ExecutableAssetReference]:
    allowed: list[ExecutableAssetReference] = []
    seen: set[tuple[str, int, int | None]] = set()
    for decision in decisions:
        for asset in decision.selected_assets:
            executable_type: Literal[
                RetrievalResourceType.METRIC,
                RetrievalResourceType.DIMENSION,
            ]
            if asset.asset_type == RetrievalResourceType.METRIC:
                executable_type = RetrievalResourceType.METRIC
            elif asset.asset_type == RetrievalResourceType.DIMENSION:
                executable_type = RetrievalResourceType.DIMENSION
            else:
                continue
            key = _asset_key(asset)
            if key in seen:
                continue
            seen.add(key)
            allowed.append(
                ExecutableAssetReference(
                    asset_type=executable_type,
                    asset_id=asset.asset_id,
                    model_id=asset.model_id,
                )
            )
    return allowed


def _asset_key(asset: AssetReference) -> tuple[str, int, int | None]:
    return asset.asset_type.value, asset.asset_id, asset.model_id


__all__ = [
    "apply_semantic_clarification",
    "compatible_dimension_selections",
    "prefer_dimension_assets",
    "selection_requires_cross_model",
]
