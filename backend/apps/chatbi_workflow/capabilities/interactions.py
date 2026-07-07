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
    "ask_rewrite_clarification": InteractionSpec(
        node_name="ask_rewrite_clarification",
        name="rewrite",
        legacy_key="rewrite_response",
    ),
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
