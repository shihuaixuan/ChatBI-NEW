"""旧 Headless 检索结果到统一 RetrievalBundle 的兼容转换器。"""

from __future__ import annotations

from typing import Any, Literal, cast

from apps.retrieval.schemas import (
    AssetReference,
    ExecutableAssetReference,
    RetrievalBindings,
    RetrievalBundle,
    RetrievalChannel,
    RetrievalChannelDiagnostic,
    RetrievalChannelStatus,
    RetrievalDecision,
    RetrievalDecisionStatus,
    RetrievalDiagnostics,
    RetrievalHit,
    RetrievalPurpose,
    RetrievalRequest,
    RetrievalResourceType,
    RetrievalScores,
    RetrievalSlotDecision,
    RetrievalSourceType,
)

_GROUP_TYPES = {
    "metrics": RetrievalResourceType.METRIC,
    "dimensions": RetrievalResourceType.DIMENSION,
    "values": RetrievalResourceType.VALUE,
    "terms": RetrievalResourceType.TERM,
}
_EXECUTABLE_GROUP_TYPES: dict[
    str,
    Literal[RetrievalResourceType.METRIC, RetrievalResourceType.DIMENSION],
] = {
    "metrics": RetrievalResourceType.METRIC,
    "dimensions": RetrievalResourceType.DIMENSION,
}


def legacy_semantic_result_to_bundle(
    request: RetrievalRequest,
    raw: dict[str, Any],
    *,
    dense_status: RetrievalChannelStatus,
    dense_error_code: str | None = None,
    dense_latency_ms: float = 0,
    latency_ms: float = 0,
) -> RetrievalBundle:
    """把当前 Graph/Agent 语义包转换为可评测的统一结果。"""

    dataset_id = request.scope.dataset_ids[0] if request.scope.dataset_ids else 0
    source_id = f"headless:dataset:{dataset_id}"
    source_version = _source_version(raw)
    candidates = _candidate_groups_with_selected(raw)
    hits_by_group = {
        group: [
            _candidate_to_hit(
                item,
                resource_type=resource_type,
                source_id=source_id,
                source_version=source_version,
                rank=rank,
            )
            for rank, item in enumerate(candidates[group], start=1)
        ]
        for group, resource_type in _GROUP_TYPES.items()
    }
    global_status = _decision_status(raw)
    slot_decisions = _slot_decisions(request, candidates, raw, global_status)
    allowed_assets = _allowed_assets(raw, global_status)
    dense_candidate_count = sum(
        1
        for items in candidates.values()
        for item in items
        if str(item.get("source") or "") == "headless_metric_embedding"
    )

    diagnostics = RetrievalDiagnostics(
        strategy_version=request.strategy_version,
        index_generation=f"legacy-{raw.get('index_version') or 'unknown'}",
        channels=[
            RetrievalChannelDiagnostic(
                channel=RetrievalChannel.LEXICAL,
                status=RetrievalChannelStatus.SUCCEEDED,
                candidate_count=sum(len(items) for items in candidates.values()) - dense_candidate_count,
            ),
            RetrievalChannelDiagnostic(
                channel=RetrievalChannel.DENSE,
                status=dense_status,
                latency_ms=dense_latency_ms,
                candidate_count=dense_candidate_count,
                error_code=dense_error_code,
            ),
        ],
        total_latency_ms=latency_ms,
        degraded_reason=(
            dense_error_code
            if dense_status in {RetrievalChannelStatus.UNAVAILABLE, RetrievalChannelStatus.FAILED}
            else None
        ),
    )

    # 旧链路即使 dense 失败仍返回 hit；基线保留业务决策，同时通过 diagnostics 暴露通道故障。
    return RetrievalBundle(
        request_id=request.request_id,
        bindings=RetrievalBindings(
            metrics=hits_by_group["metrics"],
            dimensions=hits_by_group["dimensions"],
            values=hits_by_group["values"],
            terms=hits_by_group["terms"],
        ),
        decision=RetrievalDecision(
            status=global_status,
            slot_decisions=slot_decisions,
            allowed_asset_ids=allowed_assets,
            reason_codes=_reason_codes(raw),
        ),
        diagnostics=diagnostics,
    )


def _candidate_groups_with_selected(raw: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    candidate_groups = _dict_value(raw, "candidate_groups")
    selected_groups = _dict_value(raw, "selected_assets")
    result: dict[str, list[dict[str, Any]]] = {}
    for group in _GROUP_TYPES:
        items = _dict_items(candidate_groups, group)
        selected = _dict_items(selected_groups, group)
        result[group] = _deduplicate_candidates([*items, *selected])
    return result


def _deduplicate_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    positions: dict[tuple[str, int, int | None], int] = {}
    for item in items:
        asset_id = _positive_int(item.get("asset_id"))
        if asset_id is None:
            continue
        asset_type = str(item.get("asset_type") or "")
        model_id = _positive_int(item.get("model_id"))
        key = (asset_type, asset_id, model_id)
        if key in positions:
            existing = result[positions[key]]
            for field in ("name", "display_name", "description", "payload", "alias"):
                if not existing.get(field) and item.get(field):
                    existing[field] = item[field]
            continue
        positions[key] = len(result)
        result.append(item)
    return result


def _candidate_to_hit(
    item: dict[str, Any],
    *,
    resource_type: RetrievalResourceType,
    source_id: str,
    source_version: str,
    rank: int,
) -> RetrievalHit:
    asset_id = _positive_int(item.get("asset_id"))
    if asset_id is None:
        raise ValueError("旧检索候选缺少有效 asset_id")
    model_id = _positive_int(item.get("model_id"))
    source = str(item.get("source") or "legacy_headless")
    channel = (
        RetrievalChannel.DENSE
        if source == "headless_metric_embedding"
        else RetrievalChannel.LEXICAL
    )
    title = str(
        item.get("name")
        or item.get("display_name")
        or item.get("biz_name")
        or f"{resource_type.value}:{asset_id}"
    )
    return RetrievalHit(
        resource_id=f"headless:{resource_type.value.lower()}:{asset_id}",
        resource_type=resource_type,
        source_type=RetrievalSourceType.HEADLESS,
        source_id=source_id,
        source_resource_id=str(asset_id),
        unit_id=f"legacy:{resource_type.value.lower()}:{asset_id}:{item.get('matched_field') or 'candidate'}",
        content_kind=str(item.get("matched_field") or "legacy_candidate"),
        title=title,
        snippet=str(item.get("description") or ""),
        scores=RetrievalScores(final=_float_or_none(item.get("score"))),
        ranks_by_channel={channel: rank},
        matched_field=_optional_text(item.get("matched_field")),
        matched_text=_optional_text(item.get("matched_text")),
        metadata={"legacy_source": source},
        source_version=source_version,
        asset_ref=AssetReference(
            asset_type=resource_type,
            asset_id=asset_id,
            model_id=model_id,
        ),
    )


def _slot_decisions(
    request: RetrievalRequest,
    candidates: dict[str, list[dict[str, Any]]],
    raw: dict[str, Any],
    global_status: RetrievalDecisionStatus,
) -> list[RetrievalSlotDecision]:
    selected_groups = _dict_value(raw, "selected_assets")
    result: list[RetrievalSlotDecision] = []

    for index, mention in enumerate(request.intent.metric_mentions):
        result.append(
            _slot_decision(
                subquery_id=f"metric:{index}",
                purpose=RetrievalPurpose.METRIC,
                mention=mention,
                candidate_items=candidates["metrics"],
                selected_items=_dict_items(selected_groups, "metrics"),
                global_status=global_status,
            )
        )

    for index, slot in enumerate(request.intent.dimension_slots):
        result.append(
            _slot_decision(
                subquery_id=f"dimension:{index}",
                purpose=RetrievalPurpose.DIMENSION,
                mention=slot.name,
                candidate_items=candidates["dimensions"],
                selected_items=_dict_items(selected_groups, "dimensions"),
                global_status=global_status,
            )
        )

    if "time_dimension" in request.intent.required_slot_types:
        time_candidates = [item for item in candidates["dimensions"] if _is_time_candidate(item)]
        time_selected = [
            item
            for item in _dict_items(selected_groups, "dimensions")
            if _is_time_candidate(item)
        ]
        result.append(
            _slot_decision(
                subquery_id="time_dimension:0",
                purpose=RetrievalPurpose.DIMENSION,
                mention="时间",
                candidate_items=time_candidates,
                selected_items=time_selected,
                global_status=global_status,
                filter_by_mention=False,
            )
        )
    return result


def _slot_decision(
    *,
    subquery_id: str,
    purpose: RetrievalPurpose,
    mention: str,
    candidate_items: list[dict[str, Any]],
    selected_items: list[dict[str, Any]],
    global_status: RetrievalDecisionStatus,
    filter_by_mention: bool = True,
) -> RetrievalSlotDecision:
    candidates = _filter_for_mention(candidate_items, mention) if filter_by_mention else candidate_items
    selected = _filter_for_mention(selected_items, mention) if filter_by_mention else selected_items
    if not candidates and candidate_items:
        candidates = candidate_items
    candidate_refs = [_asset_reference(item) for item in candidates]
    candidate_keys = {_asset_key(item) for item in candidate_refs}
    selected_refs = [item for item in (_asset_reference(value) for value in selected) if _asset_key(item) in candidate_keys]

    if global_status == RetrievalDecisionStatus.AMBIGUOUS and purpose == RetrievalPurpose.METRIC:
        status = RetrievalDecisionStatus.AMBIGUOUS
        selected_refs = []
    elif selected_refs:
        models = {item.model_id for item in selected_refs if item.model_id is not None}
        status = (
            RetrievalDecisionStatus.CROSS_MODEL
            if global_status == RetrievalDecisionStatus.CROSS_MODEL and len(models) > 1
            else RetrievalDecisionStatus.RESOLVED
        )
    elif not candidate_refs:
        status = RetrievalDecisionStatus.MISSED
    else:
        status = RetrievalDecisionStatus.PARTIAL

    return RetrievalSlotDecision(
        subquery_id=subquery_id,
        purpose=purpose,
        status=status,
        candidate_assets=candidate_refs,
        selected_assets=selected_refs,
        reason_codes=[f"LEGACY_{status.value.upper()}"],
    )


def _allowed_assets(
    raw: dict[str, Any],
    status: RetrievalDecisionStatus,
) -> list[ExecutableAssetReference]:
    if status not in {RetrievalDecisionStatus.RESOLVED, RetrievalDecisionStatus.CROSS_MODEL}:
        return []
    selected = _dict_value(raw, "selected_assets")
    result: list[ExecutableAssetReference] = []
    seen: set[tuple[str, int, int | None]] = set()
    for group, asset_type in _EXECUTABLE_GROUP_TYPES.items():
        for item in _dict_items(selected, group):
            asset_id = _positive_int(item.get("asset_id"))
            if asset_id is None:
                continue
            ref = ExecutableAssetReference(
                asset_type=asset_type,
                asset_id=asset_id,
                model_id=_positive_int(item.get("model_id")),
            )
            key = _asset_key(ref)
            if key not in seen:
                seen.add(key)
                result.append(ref)
    return result


def _filter_for_mention(items: list[dict[str, Any]], mention: str) -> list[dict[str, Any]]:
    normalized_mention = _normalize(mention)
    if not normalized_mention:
        return items
    primary_matches = []
    for item in items:
        payload = _dict_value(item, "payload")
        aliases = item.get("alias") or payload.get("alias") or payload.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        texts = [
            item.get("name"),
            item.get("display_name"),
            item.get("biz_name"),
            *aliases,
        ]
        if any(
            normalized and (normalized in normalized_mention or normalized_mention in normalized)
            for normalized in (_normalize(value) for value in texts)
        ):
            primary_matches.append(item)
    if primary_matches:
        return primary_matches

    # 旧候选的 matched_text 有时是完整问题；这里只接受槽位文本精确相等，
    # 避免整句同时包含多个指标时把所有选中资产归到同一个槽位。
    return [item for item in items if _normalize(item.get("matched_text")) == normalized_mention]


def _asset_reference(item: dict[str, Any]) -> AssetReference:
    asset_id = _positive_int(item.get("asset_id"))
    if asset_id is None:
        raise ValueError("旧检索资产缺少有效 asset_id")
    try:
        resource_type = RetrievalResourceType(str(item.get("asset_type")))
    except ValueError as exc:
        raise ValueError(f"旧检索资产类型无效: {item.get('asset_type')}") from exc
    return AssetReference(
        asset_type=resource_type,
        asset_id=asset_id,
        model_id=_positive_int(item.get("model_id")),
    )


def _decision_status(raw: dict[str, Any]) -> RetrievalDecisionStatus:
    status = str(raw.get("status") or "")
    decision = _dict_value(raw, "decision")
    if status == "cross_model":
        return RetrievalDecisionStatus.CROSS_MODEL
    if status == "metric_ambiguous" or decision.get("status") == "ambiguous":
        return RetrievalDecisionStatus.AMBIGUOUS
    if status == "missed":
        return RetrievalDecisionStatus.MISSED
    if decision.get("status") == "infeasible":
        return RetrievalDecisionStatus.PARTIAL
    if status == "hit":
        return RetrievalDecisionStatus.RESOLVED
    return RetrievalDecisionStatus.MISSED


def _reason_codes(raw: dict[str, Any]) -> list[str]:
    decision = _dict_value(raw, "decision")
    reason_code = decision.get("reason_code")
    return [str(reason_code)] if reason_code else []


def _source_version(raw: dict[str, Any]) -> str:
    schema_version = raw.get("schema_version") or "unknown"
    index_version = raw.get("index_version") or "unknown"
    return f"schema={schema_version};index={index_version}"


def _is_time_candidate(item: dict[str, Any]) -> bool:
    text = _normalize(
        " ".join(
            str(value or "")
            for value in (item.get("name"), item.get("display_name"), item.get("biz_name"))
        )
    )
    return any(token in text for token in ("日期", "时间", "date", "time"))


def _normalize(value: Any) -> str:
    return "".join(str(value or "").lower().split())


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _asset_key(asset: AssetReference | ExecutableAssetReference) -> tuple[str, int, int | None]:
    return (str(asset.asset_type.value), asset.asset_id, asset.model_id)


def _dict_value(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        return {}
    return cast(dict[str, Any], value)


def _dict_items(container: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = container.get(key)
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]
