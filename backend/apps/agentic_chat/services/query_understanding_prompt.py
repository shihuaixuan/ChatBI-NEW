from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

SENSITIVE_KEYS = {"password", "token", "secret", "connection", "connection_string", "dsn"}


def compact_candidates_for_prompt(candidates: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    sorted_candidates = sorted(candidates, key=lambda item: item.get("score") or 0, reverse=True)
    compacted: list[dict[str, Any]] = []
    for item in sorted_candidates[:limit]:
        compacted.append({key: value for key, value in item.items() if key not in SENSITIVE_KEYS})
    return compacted


def build_query_understanding_prompts(
    *,
    current_date: str,
    question: str,
    confirmed_slots: dict[str, Any],
    datasource_summary: dict[str, Any],
    metric_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    terminology_candidates: list[dict[str, Any]],
    schema_summary: list[dict[str, Any]],
) -> tuple[str, str]:
    system_prompt = """你是 Numora 的 Query Understanding 模块。
要求：
1. 只做问题理解，不生成 SQL。
2. 只输出 JSON，不输出 Markdown、解释或额外文本。
3. 不允许编造指标、维度、表、字段或资产 ID。
4. 候选资产只作为可选证据；只有明确命中时才能引用候选中的 asset_id。
5. 多个候选资产语义相似且无法唯一确定时，不要强行选择 Top1，必须写入 ambiguous_slots。
6. 已确认槽位不可删除、不可覆盖。
7. 候选存在但置信度不足时写入 low_confidence_slots；候选冲突时写入 conflict_slots。"""
    schema = {
        "normalized_question": "string",
        "intent": "metric_query | trend_analysis | ranking_analysis | comparison_analysis | detail_query | share_analysis | anomaly_analysis | chitchat | unknown",
        "intent_confidence": 0.0,
        "slots": {
            "metrics": [],
            "dimensions": [],
            "time_range": [],
            "filters": [],
            "comparison": [],
            "top_n": [],
            "entity": [],
            "chart_type": [],
        },
        "missing_slots": [],
        "low_confidence_slots": [{"slot": "string", "raw_text": "string", "reason": "string", "candidates": []}],
        "ambiguous_slots": [{"slot": "string", "raw_text": "string", "reason": "string", "candidates": []}],
        "conflict_slots": [{"slot": "string", "raw_text": "string", "reason": "string", "sources": []}],
        "retrieval_queries": [],
        "confidence": 0.0,
        "can_answer_with_assumption": False,
    }
    user_prompt = f"""当前日期：{current_date}

用户问题：
{question}

已确认槽位：
{json.dumps(confirmed_slots, ensure_ascii=False)}

当前数据源：
{json.dumps(datasource_summary, ensure_ascii=False)}

候选指标：
{json.dumps(compact_candidates_for_prompt(metric_candidates), ensure_ascii=False)}

候选维度：
{json.dumps(compact_candidates_for_prompt(dimension_candidates), ensure_ascii=False)}

候选术语：
{json.dumps(compact_candidates_for_prompt(terminology_candidates), ensure_ascii=False)}

Schema 摘要：
{json.dumps(schema_summary, ensure_ascii=False)}

请输出严格 JSON，Schema 如下：
{json.dumps(schema, ensure_ascii=False, indent=2)}
"""
    return system_prompt, user_prompt


def parse_model_json(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1)
    elif not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def validate_asset_bindings(payload: dict[str, Any], allowed_asset_ids: set[tuple[str, int]]) -> dict[str, Any]:
    cleaned = deepcopy(payload)
    slots = cleaned.get("slots") or {}
    if not isinstance(slots, dict):
        return cleaned
    for slot_values in slots.values():
        if not isinstance(slot_values, list):
            continue
        for slot in slot_values:
            if not isinstance(slot, dict):
                continue
            asset_id = slot.get("asset_id")
            asset_type = slot.get("asset_type")
            if asset_id is None or asset_type is None:
                continue
            if (str(asset_type), int(asset_id)) not in allowed_asset_ids:
                # 模型给出候选外 asset_id 时降级为自然语言槽位，避免资产幻觉进入 SQL。
                slot.pop("asset_id", None)
                slot["confidence"] = min(float(slot.get("confidence") or 0), 0.49)
    return cleaned
