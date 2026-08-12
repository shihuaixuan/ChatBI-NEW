"""Agent 与 Graph 共用的维度候选规范化规则。"""

from __future__ import annotations

import re
from typing import Any


def normalize_dimension_candidates(
    dimensions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按自然语言名称去重，并补齐别名、值类型和时间维度标记。"""

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in dimensions:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("dimension") or "").strip()
        if not name:
            continue
        key = dimension_text_key(name)
        if key in seen:
            continue
        seen.add(key)
        aliases = unique_texts(item.get("aliases") or item.get("alias") or [])
        data_type = str(
            item.get("data_type") or item.get("dataType") or ""
        ).strip()
        semantic_type = str(
            item.get("semantic_type") or item.get("semanticType") or ""
        ).strip()
        is_time = bool(item.get("is_time"))
        candidates.append(
            {
                "name": name,
                "aliases": aliases,
                "data_type": data_type or None,
                "semantic_type": semantic_type or None,
                "value_kind": item.get("value_kind")
                or infer_dimension_value_kind(
                    name,
                    data_type,
                    semantic_type,
                    is_time,
                ),
                "is_time": is_time,
            }
        )
    return candidates


def dimension_candidate_from_schema_element(
    dimension: Any,
) -> dict[str, Any] | None:
    """把语义 Schema 维度投影为问题理解所需的自然语言候选。"""

    name = str(getattr(dimension, "name", "") or "").strip()
    if not name:
        return None
    aliases = unique_texts(
        [
            *(getattr(dimension, "alias", []) or []),
            *dimension_name_variants(name),
        ]
    )
    ext_info = getattr(dimension, "ext_info", {}) or {}
    type_params = getattr(dimension, "type_params", {}) or {}
    data_type = str(
        getattr(dimension, "data_type", None)
        or ext_info.get("dimension_data_type")
        or ext_info.get("data_type")
        or ext_info.get("dataType")
        or ""
    ).strip()
    semantic_type = str(
        ext_info.get("semantic_type") or ext_info.get("semanticType") or ""
    ).strip()
    is_time = bool(ext_info.get("is_default_time")) or str(
        ext_info.get("dimension_type") or ""
    ).lower().endswith("time")
    is_time = is_time or bool(type_params.get("timeGranularity"))
    return {
        "name": name,
        "aliases": aliases,
        "data_type": data_type or None,
        "semantic_type": semantic_type or ("time" if is_time else None),
        "value_kind": infer_dimension_value_kind(
            name,
            data_type,
            semantic_type,
            is_time,
        ),
        "is_time": is_time,
    }


def infer_dimension_value_kind(
    name: str,
    data_type: str | None,
    semantic_type: str | None,
    is_time: bool,
) -> str:
    if is_time:
        return "date"
    normalized_semantic = str(semantic_type or "").lower()
    if normalized_semantic in {"identifier", "id"}:
        return "numeric_id"
    if normalized_semantic in {"name", "label"}:
        return "string_label"
    normalized_type = str(data_type or "").lower()
    if any(
        token in normalized_type
        for token in (
            "int",
            "number",
            "numeric",
            "decimal",
            "bigint",
            "smallint",
        )
    ):
        return "numeric_id"
    if name.endswith(("ID", "id", "编号")):
        return "numeric_id"
    return "string_label"


def dimension_name_variants(name: str) -> list[str]:
    text = name.strip()
    variants: list[str] = []
    descriptive_name = re.split(
        r"[,，]\s*(?:例如|比如|如)",
        text,
        maxsplit=1,
    )[0].strip()
    if descriptive_name and descriptive_name != text:
        # 语义层名称可能附带枚举示例，模型通常只返回示例前的业务名称。
        variants.append(descriptive_name)
    for suffix in ("ID", "id", "编号", "名称", "维度"):
        if text.endswith(suffix) and len(text) > len(suffix):
            variants.append(text[: -len(suffix)].strip())
    return variants


def unique_texts(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else [value]
    texts: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        key = dimension_text_key(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        texts.append(text)
    return texts


def dimension_text_key(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def dimension_candidate_by_text(
    dimensions: list[dict[str, Any]],
    *,
    include_time: bool = True,
) -> dict[str, dict[str, Any]]:
    candidates = normalize_dimension_candidates(dimensions)
    by_text: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not include_time and candidate.get("is_time"):
            continue
        for text in [candidate.get("name"), *(candidate.get("aliases") or [])]:
            key = dimension_text_key(text)
            if key:
                by_text[key] = candidate
    return by_text


__all__ = [
    "dimension_candidate_by_text",
    "dimension_candidate_from_schema_element",
    "dimension_name_variants",
    "dimension_text_key",
    "infer_dimension_value_kind",
    "normalize_dimension_candidates",
    "unique_texts",
]
