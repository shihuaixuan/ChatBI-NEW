"""统一 RetrievalBundle 与现有 Graph/Agent 语义 payload 的双向投影。"""

from __future__ import annotations

from typing import Any, Literal, cast

from apps.retrieval.models.dto import (
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
from apps.semantic import normalize_time_range_payload
from apps.semantic.models.dto import DatasetSchema, SchemaElement

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


def semantic_payload_to_bundle(
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
        if _uses_dense_channel(item)
    )

    diagnostics = RetrievalDiagnostics(
        strategy_version=request.strategy_version,
        index_generation=str(
            (raw.get("retrieval_diagnostics") or {}).get("index_generation")
            or raw.get("index_version")
            or "not-observed"
        ),
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

    # 评测转换保留业务决策，同时通过 diagnostics 暴露通道故障。
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


def bundle_to_semantic_payload(
    request: RetrievalRequest,
    bundle: RetrievalBundle,
    schema: DatasetSchema,
) -> dict[str, Any]:
    """把 Bundle 投影为 Graph/Agent payload，执行事实始终来自 Semantic schema。"""

    elements = _schema_elements_by_asset(schema)
    group_hits = {
        "metrics": bundle.bindings.metrics,
        "dimensions": bundle.bindings.dimensions,
        "values": bundle.bindings.values,
        "terms": bundle.bindings.terms,
    }
    candidate_groups = {
        group: [_hit_to_candidate(hit, elements) for hit in hits if hit.asset_ref is not None]
        for group, hits in group_hits.items()
    }
    candidates_by_key = {
        _candidate_key(candidate): candidate
        for candidates in candidate_groups.values()
        for candidate in candidates
    }
    selected_assets: dict[str, list[dict[str, Any]]] = {
        "metrics": [],
        "dimensions": [],
        "values": [],
        "terms": [],
    }
    group_by_type = {
        RetrievalResourceType.METRIC: "metrics",
        RetrievalResourceType.DIMENSION: "dimensions",
        RetrievalResourceType.VALUE: "values",
        RetrievalResourceType.TERM: "terms",
    }
    for slot in bundle.decision.slot_decisions:
        for asset in slot.selected_assets:
            group = group_by_type.get(asset.asset_type)
            if group is None:
                continue
            candidate = candidates_by_key.get(
                (asset.asset_type.value, asset.asset_id, asset.model_id)
            )
            if candidate is None:
                candidate = _asset_ref_to_candidate(asset, elements)
            if not any(_candidate_key(item) == _candidate_key(candidate) for item in selected_assets[group]):
                selected_assets[group].append(candidate)

    selected_with_dimensions = _selected_assets_with_dimension_groups(selected_assets)
    intent = request.intent.model_dump(mode="json")
    slot_bindings = _slot_bindings(selected_assets, intent)
    multi_query_plans = (
        _cross_model_query_plans(selected_assets, intent, schema)
        if bundle.decision.status == RetrievalDecisionStatus.CROSS_MODEL
        else []
    )
    public_candidates = _public_candidate_groups(candidate_groups)
    hit, payload_status = _payload_status(bundle)
    return {
        "hit": hit,
        "status": payload_status,
        "dataset_id": request.scope.dataset_ids[0],
        "schema_version": _schema_version(schema),
        "index_version": _index_version(schema),
        "tables": _tables(schema, selected_assets),
        "fields": _fields(selected_assets),
        "metrics": [item.get("biz_name") for item in selected_assets["metrics"]],
        "dimensions": [item.get("biz_name") for item in selected_assets["dimensions"]],
        "terms": [item.get("biz_name") for item in selected_assets["terms"]],
        "examples": [],
        "candidate_groups": public_candidates,
        "selected_assets": selected_with_dimensions,
        "slot_bindings": slot_bindings,
        "subject_domain": request.intent.subject_domain,
        "decision": {
            "status": bundle.decision.status.value,
            "strategy": "semantic_binding",
            "reason_codes": bundle.decision.reason_codes,
        },
        "ambiguities": [
            {
                "type": next(
                    (
                        slot.purpose.value
                        for slot in bundle.decision.slot_decisions
                        if slot.subquery_id == ambiguity.subquery_id
                    ),
                    "semantic_slot",
                ),
                "reason_code": ambiguity.reason_code,
                "candidates": [
                    candidates_by_key.get(
                        (asset.asset_type.value, asset.asset_id, asset.model_id)
                    )
                    or _asset_ref_to_candidate(asset, elements)
                    for asset in ambiguity.candidate_assets
                ],
            }
            for ambiguity in bundle.decision.ambiguities
        ],
        "multi_query_plans": multi_query_plans,
        "allowed_asset_ids": [
            item.model_dump(mode="json") for item in bundle.decision.allowed_asset_ids
        ],
        "retrieval_strategy_version": bundle.diagnostics.strategy_version,
        "retrieval_diagnostics": bundle.diagnostics.model_dump(mode="json"),
    }


def _schema_elements_by_asset(
    schema: DatasetSchema,
) -> dict[tuple[str, int], SchemaElement]:
    result: dict[tuple[str, int], SchemaElement] = {}
    for asset_type, elements in (
        (RetrievalResourceType.METRIC, schema.metrics),
        (RetrievalResourceType.DIMENSION, schema.dimensions),
        (RetrievalResourceType.VALUE, schema.dimension_values),
        (RetrievalResourceType.TERM, schema.terms),
    ):
        for element in elements:
            result[(asset_type.value, element.id)] = element
    return result


def _hit_to_candidate(
    hit: RetrievalHit,
    elements: dict[tuple[str, int], SchemaElement],
) -> dict[str, Any]:
    if hit.asset_ref is None:
        raise ValueError("语义资产命中缺少 asset_ref")
    candidate = _asset_ref_to_candidate(hit.asset_ref, elements)
    score = next(
        (
            value
            for value in (
                hit.scores.rerank,
                hit.scores.exact,
                hit.scores.alias,
                hit.scores.lexical,
                hit.scores.dense,
                hit.scores.final,
            )
            if value is not None
        ),
        0.0,
    )
    candidate.update(
        {
            "source": "semantic_binding",
            "score": float(score),
            "matched_text": hit.matched_text,
            "matched_field": hit.matched_field,
            "retrieval_scores": hit.scores.model_dump(mode="json"),
            "retrieval_ranks": {
                channel.value: rank for channel, rank in hit.ranks_by_channel.items()
            },
        }
    )
    return candidate


def _asset_ref_to_candidate(
    asset: AssetReference,
    elements: dict[tuple[str, int], SchemaElement],
) -> dict[str, Any]:
    element = elements.get((asset.asset_type.value, asset.asset_id))
    if element is None:
        raise ValueError(
            f"SELECTED_ASSET_NOT_IN_SEMANTIC_SCHEMA:{asset.asset_type.value}:{asset.asset_id}"
        )
    return {
        "source": "semantic_binding",
        "asset_type": asset.asset_type.value,
        "asset_id": asset.asset_id,
        "model_id": asset.model_id,
        "name": element.name,
        "display_name": element.name,
        "biz_name": element.biz_name,
        "description": element.description,
        "score": 0.0,
        "payload": element.model_dump(mode="json"),
    }


def _candidate_key(candidate: dict[str, Any]) -> tuple[str, int, int | None]:
    return (
        str(candidate.get("asset_type") or ""),
        int(candidate["asset_id"]),
        _positive_int(candidate.get("model_id")),
    )


def _payload_status(bundle: RetrievalBundle) -> tuple[bool, str]:
    status = bundle.decision.status
    if status == RetrievalDecisionStatus.CROSS_MODEL:
        return True, "cross_model"
    if status == RetrievalDecisionStatus.AMBIGUOUS:
        return True, "metric_ambiguous"
    if status == RetrievalDecisionStatus.RESOLVED:
        return True, "hit"
    if status == RetrievalDecisionStatus.DEGRADED and bundle.decision.allowed_asset_ids:
        return True, "hit"
    return False, "missed"


def _selected_assets_with_dimension_groups(
    selected_assets: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    dimensions = selected_assets.get("dimensions", [])
    grouped = dict(selected_assets)
    grouped["business_dimensions"] = [
        item for item in dimensions if not _is_time_payload(item.get("payload") or {})
    ]
    grouped["time_dimensions"] = [
        item for item in dimensions if _is_time_payload(item.get("payload") or {})
    ]
    return grouped


def _slot_bindings(
    selected_assets: dict[str, list[dict[str, Any]]],
    intent: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    dimensions = selected_assets.get("dimensions", [])
    business_dimensions = [
        item for item in dimensions if not _is_time_payload(item.get("payload") or {})
    ]
    time_dimensions = [
        item for item in dimensions if _is_time_payload(item.get("payload") or {})
    ]
    value_filters = [_asset_binding(item, "VALUE") for item in selected_assets.get("values", [])]
    dimension_filters = _dimension_filter_bindings(selected_assets, intent)
    time_filter = _time_filter_binding(time_dimensions, intent)
    time_filters = [time_filter] if time_filter is not None else []
    return {
        "metrics": [_asset_binding(item, "METRIC") for item in selected_assets.get("metrics", [])],
        "dimensions": [_asset_binding(item, "DIMENSION") for item in dimensions],
        "business_dimensions": [
            _asset_binding(item, "DIMENSION") for item in business_dimensions
        ],
        "time_dimensions": [_asset_binding(item, "DIMENSION") for item in time_dimensions],
        "group_dimensions": _group_dimension_bindings(business_dimensions, intent),
        "value_filters": value_filters,
        "dimension_filters": dimension_filters,
        "time_filters": time_filters,
        "filters": [*value_filters, *dimension_filters, *time_filters],
    }


def _asset_binding(item: dict[str, Any], asset_type: str) -> dict[str, Any]:
    return {
        "asset_type": asset_type,
        "asset_id": item["asset_id"],
        "display_name": item["name"],
        "biz_name": item["biz_name"],
        "confidence": float(item.get("score") or 0),
        "source": item.get("source") or "semantic_binding",
    }


def _group_dimension_bindings(
    dimensions: list[dict[str, Any]],
    intent: dict[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for slot in intent.get("dimension_slots") or []:
        if not isinstance(slot, dict):
            continue
        if str(slot.get("role") or "").lower() not in {"group_by", "display"}:
            continue
        dimension = _match_dimension(str(slot.get("name") or ""), dimensions)
        if dimension is None or int(dimension["asset_id"]) in seen:
            continue
        seen.add(int(dimension["asset_id"]))
        result.append(_asset_binding(dimension, "DIMENSION"))
    return result


def _dimension_filter_bindings(
    selected_assets: dict[str, list[dict[str, Any]]],
    intent: dict[str, Any],
) -> list[dict[str, Any]]:
    slots = [
        item
        for item in intent.get("dimension_slots") or []
        if isinstance(item, dict)
    ]
    for mention in intent.get("filter_mentions") or []:
        if not isinstance(mention, dict):
            continue
        slots.append(
            {
                "name": mention.get("name")
                or mention.get("dimension")
                or mention.get("field"),
                "role": "filter",
                "value": mention.get("value"),
                "value_status": "provided",
                "operator": mention.get("operator") or "=",
                "source": "intent_filter_mention",
            }
        )
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for slot in slots:
        if str(slot.get("role") or "").lower() != "filter":
            continue
        if str(slot.get("value_status") or "").lower() != "provided":
            continue
        value = slot.get("value")
        dimension = _match_dimension(
            str(slot.get("name") or ""),
            selected_assets.get("dimensions", []),
        )
        if value in (None, "") or dimension is None:
            continue
        operator = str(slot.get("operator") or "=")
        key = (int(dimension["asset_id"]), operator, str(value))
        if key in seen:
            continue
        seen.add(key)
        binding = _asset_binding(dimension, "DIMENSION")
        binding.update(
            {
                "operator": operator,
                "value": value,
                "source": slot.get("source") or "intent_dimension_slot",
            }
        )
        result.append(binding)
    return result


def _time_filter_binding(
    time_dimensions: list[dict[str, Any]],
    intent: dict[str, Any],
) -> dict[str, Any] | None:
    time_range = intent.get("time_range")
    if not isinstance(time_range, dict):
        return None
    if str(time_range.get("value_status") or "").lower() != "provided":
        return None
    normalized = normalize_time_range_payload(time_range).get("normalized")
    if not isinstance(normalized, dict) or normalized.get("kind") == "unsupported":
        return None
    if not time_dimensions:
        return None
    dimension = sorted(
        time_dimensions,
        key=lambda item: (
            -int(bool((item.get("payload") or {}).get("ext_info", {}).get("is_default_time"))),
            int(item.get("asset_id") or 0),
        ),
    )[0]
    binding = _asset_binding(dimension, "DIMENSION")
    binding.update({"operator": "=", "value": normalized, "source": "intent_time_range"})
    return binding


def _cross_model_query_plans(
    selected_assets: dict[str, list[dict[str, Any]]],
    intent: dict[str, Any],
    schema: DatasetSchema,
) -> list[dict[str, Any]]:
    metrics_by_model: dict[int, list[dict[str, Any]]] = {}
    for metric in selected_assets.get("metrics", []):
        model_id = _positive_int(metric.get("model_id"))
        if model_id is not None:
            metrics_by_model.setdefault(model_id, []).append(metric)
    plans: list[dict[str, Any]] = []
    for model_id in sorted(metrics_by_model):
        metrics = metrics_by_model[model_id]
        dimensions = _assets_for_model(
            selected_assets.get("dimensions", []),
            schema.dimensions,
            RetrievalResourceType.DIMENSION,
            model_id,
        )
        values = _assets_for_model(
            selected_assets.get("values", []),
            schema.dimension_values,
            RetrievalResourceType.VALUE,
            model_id,
        )
        model_assets = {
            "metrics": metrics,
            "dimensions": dimensions,
            "values": values,
            "terms": [],
        }
        plans.append(
            {
                "model_id": model_id,
                "metric_ids": [int(item["asset_id"]) for item in metrics],
                "dimension_ids": [int(item["asset_id"]) for item in dimensions],
                "metrics": [str(item.get("name") or item.get("biz_name") or "") for item in metrics],
                "dimensions": [
                    str(item.get("name") or item.get("biz_name") or "")
                    for item in dimensions
                ],
                "slots": _slot_bindings(model_assets, intent),
                "selected_assets": model_assets,
                "incompatible_dimensions": [],
            }
        )
    return plans


def _assets_for_model(
    selected: list[dict[str, Any]],
    schema_elements: list[SchemaElement],
    asset_type: RetrievalResourceType,
    model_id: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in selected:
        if _positive_int(item.get("model_id")) == model_id:
            result.append(item)
            continue
        names = _candidate_names(item)
        equivalent = next(
            (
                element
                for element in schema_elements
                if element.model == model_id and names & _element_names(element)
            ),
            None,
        )
        if equivalent is None:
            continue
        candidate = _asset_ref_to_candidate(
            AssetReference(
                asset_type=asset_type,
                asset_id=equivalent.id,
                model_id=model_id,
            ),
            {(asset_type.value, equivalent.id): equivalent},
        )
        candidate["score"] = float(item.get("score") or 0)
        result.append(candidate)
    return result


def _match_dimension(
    name: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    normalized = _normalize_text(name)
    if not normalized:
        return None
    for candidate in candidates:
        if any(
            normalized == value or normalized in value or value in normalized
            for value in _candidate_names(candidate)
        ):
            return candidate
    return None


def _candidate_names(candidate: dict[str, Any]) -> set[str]:
    raw_payload = candidate.get("payload")
    payload = cast(dict[str, Any], raw_payload) if isinstance(raw_payload, dict) else {}
    aliases = payload.get("alias") or payload.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    return {
        normalized
        for value in (
            candidate.get("name"),
            candidate.get("biz_name"),
            candidate.get("matched_text"),
            payload.get("name"),
            payload.get("biz_name"),
            payload.get("bizName"),
            *aliases,
        )
        if (normalized := _normalize_text(value))
    }


def _element_names(element: SchemaElement) -> set[str]:
    return {
        normalized
        for value in (element.name, element.biz_name, *(element.alias or []))
        if (normalized := _normalize_text(value))
    }


def _public_candidate_groups(
    candidate_groups: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        group: [
            {key: value for key, value in candidate.items() if key != "payload"}
            for candidate in candidates
        ]
        for group, candidates in candidate_groups.items()
    }


def _is_time_payload(payload: dict[str, Any]) -> bool:
    raw_ext_info = payload.get("ext_info")
    ext_info = cast(dict[str, Any], raw_ext_info) if isinstance(raw_ext_info, dict) else {}
    dimension_type = str(ext_info.get("dimension_type") or "").lower()
    semantic_type = str(ext_info.get("semantic_type") or "").lower()
    data_type = str(ext_info.get("dimension_data_type") or "").lower()
    return bool(
        ext_info.get("is_default_time")
        or ext_info.get("time_granularities")
        or dimension_type in {"time", "partition_time"}
        or semantic_type == "time"
        or any(token in data_type for token in ("date", "time", "timestamp"))
    )


def _tables(
    schema: DatasetSchema,
    selected_assets: dict[str, list[dict[str, Any]]],
) -> list[str]:
    model_ids = {
        item.get("model_id")
        for items in selected_assets.values()
        for item in items
        if item.get("model_id") is not None
    }
    return list(
        dict.fromkeys(
            str(model.get("tableQuery") or "").strip()
            for model in schema.models
            if model.get("id") in model_ids and str(model.get("tableQuery") or "").strip()
        )
    )


def _fields(selected_assets: dict[str, list[dict[str, Any]]]) -> list[str]:
    return list(
        dict.fromkeys(
            str(field)
            for item in selected_assets.get("metrics", [])
            for field in (item.get("payload") or {}).get("fields") or []
        )
    )


def _schema_version(schema: DatasetSchema) -> int | None:
    return _positive_int(schema.data_set.ext_info.get("schema_version"))


def _index_version(schema: DatasetSchema) -> int | None:
    return _positive_int(schema.data_set.ext_info.get("index_version"))


def _normalize_text(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


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
        raise ValueError("语义检索候选缺少有效 asset_id")
    model_id = _positive_int(item.get("model_id"))
    source = str(item.get("source") or "semantic_binding")
    channel = RetrievalChannel.DENSE if _uses_dense_channel(item) else RetrievalChannel.LEXICAL
    title = str(
        item.get("name")
        or item.get("display_name")
        or item.get("biz_name")
        or f"{resource_type.value}:{asset_id}"
    )
    return RetrievalHit(
        resource_id=f"headless:{resource_type.value.lower()}:{asset_id}",
        resource_type=resource_type,
        source_type=RetrievalSourceType.SEMANTIC,
        source_id=source_id,
        source_resource_id=str(asset_id),
        unit_id=f"semantic:{resource_type.value.lower()}:{asset_id}:{item.get('matched_field') or 'candidate'}",
        content_kind=str(item.get("matched_field") or "semantic_candidate"),
        title=title,
        snippet=str(item.get("description") or ""),
        scores=RetrievalScores(final=_float_or_none(item.get("score"))),
        ranks_by_channel={channel: rank},
        matched_field=_optional_text(item.get("matched_field")),
        matched_text=_optional_text(item.get("matched_text")),
        metadata={"candidate_source": source},
        source_version=source_version,
        asset_ref=AssetReference(
            asset_type=resource_type,
            asset_id=asset_id,
            model_id=model_id,
        ),
    )


def _uses_dense_channel(item: dict[str, Any]) -> bool:
    scores = item.get("retrieval_scores")
    return isinstance(scores, dict) and scores.get("dense") is not None


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
