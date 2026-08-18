"""R0 问题理解输出的确定性归一化与字段级修复工具。"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class PayloadNormalizationResult:
    """归一化结果及被剥离字段的审计摘要。"""

    payload: dict[str, Any]
    dropped_fields: tuple[str, ...] = ()


def normalize_model_payload(
    payload: dict[str, Any],
    *,
    stage: str,
    model_type: type[BaseModel],
    temporal_authority_enabled: bool = False,
) -> PayloadNormalizationResult:
    """在 Pydantic 校验前执行确定性字段归一化。

    R0 只处理结构路径级的格式噪声，不转换业务字段名称。
    """

    normalized = deepcopy(payload)
    dropped: list[str] = []

    # 顶层额外字段不能让整份合法语义失效；字段路径会写入 Trace 账本。
    allowed_fields = set(model_type.model_fields)
    for key in list(normalized):
        if key not in allowed_fields and key != "repair_feedback":
            normalized.pop(key, None)
            dropped.append(key)

    # DTO 的嵌套模型同样采用 extra=forbid；在校验前按同一份 JSON Schema
    # 确定性剥离嵌套冗余字段，避免把局部格式噪声升级为整份语义失败。
    schema = model_type.model_json_schema()
    _strip_nested_schema_extras(
        normalized,
        schema,
        root_schema=schema,
        path="",
        dropped_fields=dropped,
    )

    if stage == "QUESTION_UNDERSTANDING":
        _normalize_comparison_fields(normalized, dropped)
        if temporal_authority_enabled:
            # Temporal 是时间事实唯一来源，模型输出的旧时间字段只做审计剥离。
            for key in ("time_range", "time_ranges", "comparison"):
                if key in normalized:
                    normalized.pop(key, None)
                    dropped.append(key)
            query_shape = normalized.get("query_shape")
            if isinstance(query_shape, dict) and "comparison_type" in query_shape:
                query_shape.pop("comparison_type", None)
                dropped.append("query_shape.comparison_type")

    return PayloadNormalizationResult(
        payload=normalized,
        dropped_fields=tuple(dict.fromkeys(dropped)),
    )


def _strip_nested_schema_extras(
    value: Any,
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any],
    path: str,
    dropped_fields: list[str],
) -> None:
    """递归处理 Pydantic JSON Schema 中 additionalProperties=false 的对象。"""

    schema = _resolve_schema_ref(schema, root_schema)
    choices = schema.get("anyOf") or schema.get("oneOf")
    if choices:
        if value is None:
            return
        # 对联合对象优先选择能解释当前键的对象分支；字符串、数字等分支不递归。
        object_choices = []
        for choice in choices:
            resolved_choice = _resolve_schema_ref(choice, root_schema)
            if "properties" in resolved_choice or resolved_choice.get("type") == "object":
                object_choices.append(resolved_choice)
        if isinstance(value, dict) and object_choices:
            for choice in object_choices:
                if not choice.get("properties") or any(
                    key in choice["properties"] for key in value
                ):
                    _strip_nested_schema_extras(
                        value,
                        choice,
                        root_schema=root_schema,
                        path=path,
                        dropped_fields=dropped_fields,
                    )
                    return
        return

    if isinstance(value, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            if schema.get("additionalProperties") is False:
                for key in list(value):
                    if key not in properties:
                        dropped_fields.append(_join_path(path, key))
                        value.pop(key, None)
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    _strip_nested_schema_extras(
                        value[key],
                        child_schema,
                        root_schema=root_schema,
                        path=_join_path(path, key),
                        dropped_fields=dropped_fields,
                    )
        return

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _strip_nested_schema_extras(
                    item,
                    item_schema,
                    root_schema=root_schema,
                    path=f"{path}[{index}]",
                    dropped_fields=dropped_fields,
                )


def _resolve_schema_ref(
    schema: dict[str, Any],
    root_schema: dict[str, Any],
) -> dict[str, Any]:
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
        return schema
    definition_name = reference.removeprefix("#/$defs/")
    definitions = root_schema.get("$defs")
    definition = definitions.get(definition_name) if isinstance(definitions, dict) else None
    return definition if isinstance(definition, dict) else schema


def _join_path(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def _normalize_comparison_fields(
    payload: dict[str, Any],
    dropped_fields: list[str],
) -> None:
    """清理比较对象中的旧别名和冗余字段，不改变比较主体。"""

    comparison = payload.get("comparison")
    if not isinstance(comparison, dict):
        return
    aliases = {
        "year_over_year": "yoy",
        "year-over-year": "yoy",
        "同比": "yoy",
        "month_over_month": "mom",
        "month-over-month": "mom",
        "环比": "mom",
        "difference": "custom",
        "growth": "custom",
        "change": "custom",
        "percent_change": "custom",
        "percentage_change": "custom",
        "change_rate": "custom",
        "增长率": "custom",
    }
    method = str(comparison.get("method") or comparison.get("type") or "")
    normalized_method = aliases.get(method.strip().lower())
    if normalized_method:
        comparison["method"] = normalized_method
    for key in ("type", "base_time", "compare_time", "target"):
        if key in comparison:
            comparison.pop(key, None)
            dropped_fields.append(f"comparison.{key}")
    compare = comparison.get("compare")
    if isinstance(compare, str):
        comparison["compare"] = [compare]


def build_patch_request(
    *,
    stage: str,
    payload: dict[str, Any],
    validation_error: ValidationError,
    model_type: type[BaseModel],
) -> dict[str, Any]:
    """只向补丁模型暴露失败路径、原值和对应的字段契约。"""

    failures: list[dict[str, Any]] = []
    for error in validation_error.errors():
        location = _path_from_location(error.get("loc") or ())
        failures.append(
            {
                "path": location,
                "value": _get_path(payload, location),
                "error_type": error.get("type"),
                "message": error.get("msg"),
            }
        )
    return {
        "stage": stage,
        "failed_fields": failures,
        "schema": model_type.model_json_schema(),
    }


def apply_field_patches(
    payload: dict[str, Any],
    patches: dict[str, Any],
) -> dict[str, Any]:
    """按 JSON 路径合并字段补丁，禁止补丁替换整份对象。"""

    result = deepcopy(payload)
    for path, value in patches.items():
        if not isinstance(path, str) or not path.strip():
            raise ValueError("SEMANTIC_PATCH_PATH_INVALID")
        _set_path(result, path, value)
    return result


def assert_repair_invariants(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    stage: str,
) -> None:
    """确保字段修复没有静默删除或替换核心语义。"""

    if stage != "QUESTION_UNDERSTANDING":
        return
    for key in ("metric_mentions", "time_mentions", "dimension_mentions"):
        before_values = before.get(key)
        after_values = after.get(key)
        if isinstance(before_values, list) and isinstance(after_values, list):
            if len(after_values) < len(before_values):
                raise ValueError(f"SEMANTIC_REPAIR_INVARIANT_VIOLATION:{key}")
            if set(map(str, before_values)) != set(map(str, after_values)):
                raise ValueError(f"SEMANTIC_REPAIR_INVARIANT_VIOLATION:{key}")

    before_ranges = before.get("time_ranges")
    after_ranges = after.get("time_ranges")
    if isinstance(before_ranges, list) and isinstance(after_ranges, list):
        if len(after_ranges) < len(before_ranges):
            raise ValueError("SEMANTIC_REPAIR_INVARIANT_VIOLATION:time_ranges")
    if before.get("comparison") is not None and after.get("comparison") is None:
        raise ValueError("SEMANTIC_REPAIR_INVARIANT_VIOLATION:comparison")


def parse_patch_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """校验补丁模型的最小输出结构。"""

    patches = payload.get("patches")
    if not isinstance(patches, dict):
        raise ValueError("SEMANTIC_PATCHES_REQUIRED")
    return patches


def _path_from_location(location: tuple[Any, ...]) -> str:
    parts: list[str] = []
    for item in location:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(f"[{item}]")
        else:
            parts.append(str(item))
    return ".".join(parts)


def _path_parts(path: str) -> list[str | int]:
    parts: list[str | int] = []
    for item in path.split("."):
        match = re.fullmatch(r"([^\[]+)(?:\[(\d+)\])?", item)
        if match is None:
            raise ValueError("SEMANTIC_PATCH_PATH_INVALID")
        parts.append(match.group(1))
        if match.group(2) is not None:
            parts.append(int(match.group(2)))
    return parts


def _get_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    try:
        for part in _path_parts(path):
            current = current[part]
    except (KeyError, IndexError, TypeError):
        return None
    return current


def _set_path(payload: dict[str, Any], path: str, value: Any) -> None:
    parts = _path_parts(path)
    current: Any = payload
    for index, part in enumerate(parts[:-1]):
        next_part = parts[index + 1]
        if isinstance(current, list):
            if not isinstance(part, int) or part >= len(current):
                raise ValueError("SEMANTIC_PATCH_PATH_NOT_FOUND")
            current = current[part]
            continue
        if part not in current:
            current[part] = [] if isinstance(next_part, int) else {}
        current = current[part]
    last = parts[-1]
    if isinstance(current, list):
        if not isinstance(last, int) or last >= len(current):
            raise ValueError("SEMANTIC_PATCH_PATH_NOT_FOUND")
        current[last] = value
    else:
        current[last] = value


__all__ = [
    "PayloadNormalizationResult",
    "apply_field_patches",
    "assert_repair_invariants",
    "build_patch_request",
    "normalize_model_payload",
    "parse_patch_payload",
]
