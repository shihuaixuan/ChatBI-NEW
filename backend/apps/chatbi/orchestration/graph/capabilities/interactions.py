from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class InteractionSpec:
    """ChatBI v1 单个交互节点的上下文写入规格。"""

    node_name: str
    name: str
    legacy_key: str
    max_rounds: int = 2

    @property
    def standard_path(self) -> str:
        return standard_interaction_path(self.node_name)

    @property
    def legacy_path(self) -> str:
        return f"variables.{self.legacy_key}"


CHATBI_V1_INTERACTION_SPECS: dict[str, InteractionSpec] = {
    "ask_intent_clarification": InteractionSpec(
        node_name="ask_intent_clarification",
        name="intent",
        legacy_key="intent_response",
    ),
    "ask_slot_clarification": InteractionSpec(
        node_name="ask_slot_clarification",
        name="slot",
        legacy_key="slot_response",
    ),
    "ask_cross_model_split": InteractionSpec(
        node_name="ask_cross_model_split",
        name="cross_model",
        legacy_key="cross_model_response",
    ),
    "ask_metric_selection": InteractionSpec(
        node_name="ask_metric_selection",
        name="metric",
        legacy_key="metric_selection",
    ),
}


def interaction_spec(node_name: str) -> InteractionSpec | None:
    return CHATBI_V1_INTERACTION_SPECS.get(node_name)


def standard_interaction_path(node_name: str) -> str:
    return f"variables.interactions.{node_name}"


def legacy_interaction_path(node_name: str) -> str | None:
    spec = interaction_spec(node_name)
    return spec.legacy_path if spec is not None else None


def build_interaction_record(
    node_name: str,
    response: dict[str, Any],
    round_count: int,
    answered_at: datetime,
) -> dict[str, Any]:
    """构造标准交互记录；裸 response 只保留在兼容旧字段中。"""

    safe_round = max(1, int(round_count or 1))
    return {
        "node_name": node_name,
        "round": safe_round,
        "response": response,
        "skipped": bool(response.get("skipped") is True),
        "answered_at": answered_at.isoformat(),
    }


def read_interaction_record(variables: dict[str, Any], node_name: str) -> dict[str, Any]:
    interactions = variables.get("interactions")
    if not isinstance(interactions, dict):
        return {}
    record = interactions.get(node_name)
    return record if isinstance(record, dict) else {}


def read_interaction_response(
    variables: dict[str, Any],
    node_name: str,
    legacy_key: str | None = None,
) -> dict[str, Any]:
    record = read_interaction_record(variables, node_name)
    response = record.get("response")
    if isinstance(response, dict):
        return response
    if legacy_key:
        legacy_response = variables.get(legacy_key)
        if isinstance(legacy_response, dict):
            return legacy_response
    return {}


def apply_slot_response_to_intent(intent: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """把槽位澄清回答应用到本地 intent 视图；不修改原始上下文。"""

    updated = deepcopy(intent) if isinstance(intent, dict) else {}
    if not updated or not response or response.get("skipped") is True:
        return updated
    ambiguous_slots = updated.get("ambiguous_slots") if isinstance(updated.get("ambiguous_slots"), list) else []
    if "subject_domain" in ambiguous_slots:
        return _apply_subject_domain_response(updated, response)
    return _apply_dimension_response(updated, response)


def _apply_subject_domain_response(intent: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    domain_id = _int_or_none(response.get("domain_id") or response.get("subject_domain_id"))
    if domain_id is None:
        return intent
    domain_name = response.get("subject_domain") or response.get("domain_name") or str(domain_id)
    intent["subject_domain"] = {
        "status": "selected",
        "domain_id": domain_id,
        "domain_name": str(domain_name),
        "domain_biz_name": response.get("domain_biz_name"),
        "confidence": 1.0,
        "reason": "用户已确认主题域",
        "candidate_domain_ids": [domain_id],
    }
    intent["ambiguous_slots"] = [
        slot for slot in intent.get("ambiguous_slots", []) if slot != "subject_domain"
    ]
    return intent


def _apply_dimension_response(intent: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    dimension_name = _dimension_name(intent)
    usage = response.get("dimension_usage")
    if usage == "ignore":
        _remove_dimension(intent, dimension_name)
    elif usage == "group_by":
        _set_dimension_slot(intent, dimension_name, role="group_by", value=None, value_status="not_provided")
    else:
        dimension_values = response.get("dimension_values")
        if isinstance(dimension_values, dict):
            updated = False
            for name, raw_value in dimension_values.items():
                value = _normalize_dimension_value(str(name), raw_value)
                if value in (None, ""):
                    continue
                _set_dimension_slot(
                    intent,
                    str(name),
                    role="filter",
                    value=str(value),
                    value_status="provided",
                )
                updated = True
            if not updated:
                return intent
        else:
            dimension_value = response.get("dimension_value") or response.get("filter_value")
            dimension_value = _normalize_dimension_value(dimension_name, dimension_value)
            if dimension_value in (None, "") and response.get("dimension") not in (None, "", dimension_name):
                dimension_value = _normalize_dimension_value(dimension_name, response.get("dimension"))
            if dimension_value not in (None, ""):
                _set_dimension_slot(
                    intent,
                    dimension_name,
                    role="filter",
                    value=str(dimension_value),
                    value_status="provided",
                )
            else:
                return intent

    intent["ambiguous_slots"] = [
        slot for slot in intent.get("ambiguous_slots", []) if slot != "dimension"
    ]
    return intent


def _normalize_dimension_value(dimension_name: str, raw_value: Any) -> str | None:
    if raw_value in (None, ""):
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    dimension = str(dimension_name or "").strip()
    if not dimension:
        return value
    for separator in ("为", "=", "是", ":", "："):
        prefix = f"{dimension}{separator}"
        if value.startswith(prefix):
            return value[len(prefix):].strip() or None
    return value


def _dimension_name(intent: dict[str, Any]) -> str:
    dimension_slots = intent.get("dimension_slots") if isinstance(intent.get("dimension_slots"), list) else []
    for slot in dimension_slots:
        if isinstance(slot, dict) and slot.get("name"):
            return str(slot["name"])
    dimension_mentions = intent.get("dimension_mentions") if isinstance(intent.get("dimension_mentions"), list) else []
    if dimension_mentions:
        return str(dimension_mentions[0])
    return str(intent.get("dimension") or "维度")


def _set_dimension_slot(
    intent: dict[str, Any],
    dimension_name: str,
    role: str,
    value: Any,
    value_status: str,
) -> None:
    slot = {
        "name": dimension_name,
        "role": role,
        "value": value,
        "value_status": value_status,
    }
    dimension_slots = intent.get("dimension_slots")
    if not isinstance(dimension_slots, list):
        intent["dimension_slots"] = [slot]
        return
    for index, existing in enumerate(dimension_slots):
        if isinstance(existing, dict) and existing.get("name") == dimension_name:
            dimension_slots[index] = slot
            break
    else:
        dimension_slots.append(slot)
    intent["dimension_slots"] = dimension_slots
    dimension_mentions = intent.get("dimension_mentions")
    if isinstance(dimension_mentions, list) and dimension_name not in dimension_mentions:
        dimension_mentions.append(dimension_name)
    elif not isinstance(dimension_mentions, list):
        intent["dimension_mentions"] = [dimension_name]


def _remove_dimension(intent: dict[str, Any], dimension_name: str) -> None:
    dimension_slots = intent.get("dimension_slots")
    if isinstance(dimension_slots, list):
        intent["dimension_slots"] = [
            slot for slot in dimension_slots if not (isinstance(slot, dict) and slot.get("name") == dimension_name)
        ]
    dimension_mentions = intent.get("dimension_mentions")
    if isinstance(dimension_mentions, list):
        intent["dimension_mentions"] = [dimension for dimension in dimension_mentions if dimension != dimension_name]


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def selected_metric_from_response(
    knowledge: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any] | None:
    """从用户指标选择回答中定位候选指标；返回副本，不改写 knowledge。"""

    if not isinstance(knowledge, dict) or not isinstance(response, dict):
        return None
    if not response or response.get("skipped") is True:
        return None
    selected = _first_present(response, ("metric", "metric_id", "asset_id"))
    if selected in (None, ""):
        return None
    selected_text = str(selected)
    for candidate in _metric_candidates(knowledge):
        if _candidate_matches(candidate, selected_text):
            return _metric_asset(candidate, selected)
    return _metric_asset(selected, selected)


def prune_dimensions_for_selected_metric(
    selected_assets: dict[str, Any],
    slots: dict[str, Any],
    intent: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按用户选中指标的模型裁剪分组维度；同 biz_name 可回退到指标模型内实例。

    过滤条件保留给编译器处理。找不到同模型等价维度时丢弃该分组维度
    （完整不兼容阻断由 knowledge 层在检索阶段完成）。
    """

    dimensions = deepcopy(slots.get("dimensions")) if isinstance(slots.get("dimensions"), list) else []
    filters = deepcopy(slots.get("filters")) if isinstance(slots.get("filters"), list) else []
    metrics = _items(selected_assets.get("metrics"))
    selected_model_ids = {item.get("model_id") for item in metrics if item.get("model_id") is not None}

    dimension_assets = {
        item.get("asset_id"): item
        for item in _items(selected_assets.get("dimensions"))
        if item.get("asset_id") is not None
    }
    requested_names = _requested_dimension_names(intent)
    pruned_dimensions = []
    seen_ids: set[Any] = set()
    for item in dimensions:
        asset_id = item.get("asset_id")
        asset = dimension_assets.get(asset_id, item)
        model_id = asset.get("model_id")
        if selected_model_ids and model_id is not None and model_id not in selected_model_ids:
            # 槽位里已有同模型等价实例时，直接丢弃错模型副本，避免重复绑定。
            if _slot_list_has_metric_model_equivalent(asset, dimensions, dimension_assets, selected_model_ids):
                continue
            remapped = _remap_dimension_slot_to_metric_models(item, asset, selected_model_ids, selected_assets)
            if remapped is None:
                continue
            item = remapped
            asset = remapped
            asset_id = item.get("asset_id")
        if requested_names and not _dimension_matches_any(asset, requested_names):
            continue
        if asset_id is not None and asset_id in seen_ids:
            continue
        if asset_id is not None:
            seen_ids.add(asset_id)
        pruned_dimensions.append(item)
    return pruned_dimensions, filters


def _slot_list_has_metric_model_equivalent(
    asset: dict[str, Any],
    dimensions: list[dict[str, Any]],
    dimension_assets: dict[Any, dict[str, Any]],
    selected_model_ids: set[Any],
) -> bool:
    source_biz = str(asset.get("biz_name") or "").strip().lower()
    source_name = str(asset.get("name") or asset.get("display_name") or "").strip().lower()
    for slot in dimensions:
        candidate = dimension_assets.get(slot.get("asset_id"), slot)
        if candidate.get("model_id") not in selected_model_ids:
            continue
        cand_biz = str(candidate.get("biz_name") or slot.get("biz_name") or "").strip().lower()
        cand_name = str(
            candidate.get("name") or candidate.get("display_name") or slot.get("display_name") or ""
        ).strip().lower()
        if source_biz and cand_biz == source_biz:
            return True
        if source_name and (cand_name == source_name or cand_biz == source_name):
            return True
    return False


def _remap_dimension_slot_to_metric_models(
    slot: dict[str, Any],
    asset: dict[str, Any],
    selected_model_ids: set[Any],
    selected_assets: dict[str, Any],
) -> dict[str, Any] | None:
    """指标澄清后：把错模型维度槽位重映射到同 biz_name 的指标模型实例。"""

    source_biz = str(asset.get("biz_name") or slot.get("biz_name") or "").strip().lower()
    source_name = str(asset.get("name") or asset.get("display_name") or slot.get("display_name") or "").strip().lower()
    for candidate in _items(selected_assets.get("dimensions")):
        if candidate.get("model_id") not in selected_model_ids:
            continue
        cand_biz = str(candidate.get("biz_name") or "").strip().lower()
        cand_name = str(candidate.get("name") or candidate.get("display_name") or "").strip().lower()
        if source_biz and cand_biz == source_biz:
            remapped = deepcopy(slot)
            remapped["asset_id"] = candidate.get("asset_id")
            remapped["display_name"] = candidate.get("name") or candidate.get("display_name") or remapped.get("display_name")
            if candidate.get("model_id") is not None:
                remapped["model_id"] = candidate["model_id"]
            return remapped
        if source_name and (cand_name == source_name or cand_biz == source_name):
            remapped = deepcopy(slot)
            remapped["asset_id"] = candidate.get("asset_id")
            remapped["display_name"] = candidate.get("name") or candidate.get("display_name") or remapped.get("display_name")
            if candidate.get("model_id") is not None:
                remapped["model_id"] = candidate["model_id"]
            return remapped
    return None


def _first_present(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def _metric_candidates(knowledge: dict[str, Any]) -> list[Any]:
    candidates: list[Any] = []
    for ambiguity in knowledge.get("ambiguities", []) or []:
        if isinstance(ambiguity, dict) and ambiguity.get("type") == "metric":
            candidates.extend(ambiguity.get("candidates") or [])
    groups = knowledge.get("candidate_groups")
    if isinstance(groups, dict):
        candidates.extend(groups.get("metrics") or [])
    selected_assets = knowledge.get("selected_assets")
    if isinstance(selected_assets, dict):
        candidates.extend(selected_assets.get("metrics") or [])
    slot_bindings = knowledge.get("slot_bindings")
    if isinstance(slot_bindings, dict):
        candidates.extend(slot_bindings.get("metrics") or [])
    return candidates


def _candidate_matches(candidate: Any, selected_text: str) -> bool:
    if isinstance(candidate, dict):
        values = (
            candidate.get("asset_id"),
            candidate.get("id"),
            candidate.get("biz_name"),
            candidate.get("display_name"),
            candidate.get("name"),
            candidate.get("title"),
        )
        return any(str(value) == selected_text for value in values if value not in (None, ""))
    return str(candidate) == selected_text


def _metric_asset(candidate: Any, selected_metric: Any) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        text = str(candidate)
        return {
            "asset_id": candidate,
            "biz_name": text,
            "display_name": text,
            "source": "user_selected",
        }
    display_name = (
        candidate.get("display_name")
        or candidate.get("name")
        or candidate.get("title")
        or candidate.get("biz_name")
        or str(selected_metric)
    )
    biz_name = candidate.get("biz_name") or str(candidate.get("asset_id") or selected_metric)
    asset_id = candidate.get("asset_id") or candidate.get("id") or biz_name
    asset = {
        "asset_id": asset_id,
        "biz_name": str(biz_name),
        "display_name": str(display_name),
        "source": "user_selected",
    }
    if candidate.get("model_id") is not None:
        asset["model_id"] = candidate["model_id"]
    if isinstance(candidate.get("payload"), dict):
        asset["payload"] = deepcopy(candidate["payload"])
    return asset


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _requested_dimension_names(intent: dict[str, Any]) -> list[str]:
    names: list[str] = []
    if not isinstance(intent, dict):
        return names
    for slot in intent.get("dimension_slots") or []:
        if not isinstance(slot, dict):
            continue
        if str(slot.get("role") or "").lower() not in {"group_by", "display"}:
            continue
        name = str(slot.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _dimension_matches_any(candidate: dict[str, Any], requested_names: list[str]) -> bool:
    fields = [
        candidate.get("display_name"),
        candidate.get("name"),
        candidate.get("biz_name"),
        candidate.get("matched_text"),
    ]
    payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    fields.extend([payload.get("name"), payload.get("biz_name")])
    aliases = payload.get("alias") or payload.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    fields.extend(aliases if isinstance(aliases, list) else [])
    normalized_fields = [str(field or "").strip() for field in fields if str(field or "").strip()]
    for name in requested_names:
        normalized_name = str(name or "").strip()
        if any(normalized_name in field or field in normalized_name for field in normalized_fields):
            return True
    return False
