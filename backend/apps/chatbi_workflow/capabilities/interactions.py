from __future__ import annotations

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
