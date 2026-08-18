"""Trace 输入输出的统一脱敏规则。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import orjson

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "database_url",
        "connection_string",
    }
)
_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+")
_KEY_PATTERN = re.compile(r"\bsk-[a-zA-Z0-9_-]{8,}\b")
_SECRET_VALUE_PATTERN = re.compile(
    r'(?i)(["\'](?:api_key|apikey|authorization|cookie|password|passwd|secret|token|'
    r'access_token|refresh_token|database_url|connection_string)["\']\s*:\s*)'
    r'(["\'])[^"\']*\2'
)

# R0 观测契约：单个节点的完整输入/输出详情默认不超过 32KB。
TRACE_DETAIL_MAX_BYTES = 32 * 1024


def redact_trace_payload(value: Any) -> Any:
    """递归清除固定秘密字段和常见凭据文本。"""

    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if str(key).strip().lower() in _SECRET_KEYS
                else redact_trace_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact_trace_payload(item) for item in value]
    if isinstance(value, str):
        redacted = _SECRET_VALUE_PATTERN.sub(r'\1"[REDACTED]"', value)
        return _KEY_PATTERN.sub(
            "[REDACTED]",
            _BEARER_PATTERN.sub("Bearer [REDACTED]", redacted),
        )
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)


def redact_trace_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """将节点输入输出收敛为可序列化且已脱敏的字典。"""

    if not value:
        return {}
    redacted = redact_trace_payload(value)
    if not isinstance(redacted, dict):  # pragma: no cover - Mapping 输入必然得到字典。
        return {}
    return redacted


def redact_trace_detail(
    value: Mapping[str, Any] | None,
    *,
    max_bytes: int = TRACE_DETAIL_MAX_BYTES,
) -> dict[str, Any]:
    """脱敏并按结构裁剪节点详情，保留明确的截断标记。"""

    if not value:
        return {}
    if max_bytes <= 0:
        raise ValueError("TRACE_DETAIL_MAX_BYTES_INVALID")
    minimum_bytes = len(orjson.dumps({"_truncated": True}))
    if max_bytes < minimum_bytes:
        raise ValueError("TRACE_DETAIL_MAX_BYTES_TOO_SMALL")
    redacted = redact_trace_mapping(value)
    original_bytes = len(orjson.dumps(redacted))
    if original_bytes <= max_bytes:
        return redacted

    # 逐步缩小列表和字符串，优先保留字段名、候选前几项和错误信息。
    for list_keep, string_cap in ((12, 512), (8, 320), (5, 200), (3, 120), (1, 60), (0, 30)):
        candidate = _prune_trace_value(
            redacted,
            list_keep=list_keep,
            string_cap=string_cap,
        )
        if not isinstance(candidate, dict):
            candidate = {"value": candidate}
        candidate["_truncated"] = True
        candidate["_original_bytes"] = original_bytes
        if len(orjson.dumps(candidate)) <= max_bytes:
            return candidate

    # 极端情况下键数量本身也超过上限，按字节预算保留稳定键名摘要。
    all_keys = sorted(str(key) for key in redacted)
    base = {"_truncated": True, "_original_bytes": original_bytes}
    keys: list[str] = []
    for key in all_keys:
        candidate = {
            **base,
            "_keys": [*keys, key],
            "_truncated_key_count": len(all_keys) - len(keys) - 1,
        }
        if len(orjson.dumps(candidate)) > max_bytes:
            break
        keys.append(key)
    bounded = {
        **base,
        "_keys": keys,
        "_truncated_key_count": len(all_keys) - len(keys),
    }
    if len(orjson.dumps(bounded)) <= max_bytes:
        return bounded
    # max_bytes 可能小于完整账本字段，但仍需返回不超限的明确截断标记。
    return {"_truncated": True}


def _prune_trace_value(value: Any, *, list_keep: int, string_cap: int) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _prune_trace_value(
                item,
                list_keep=list_keep,
                string_cap=string_cap,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        # 列表内部不能插入截断标记对象。Trace 详情可能会被后续按业务 DTO
        # 重新校验，混入标记对象会把原本合法的字符串、数字或结构项变成非法输入。
        # 截断事实由外层 _truncated 和 _original_bytes 统一表达。
        return [
            _prune_trace_value(item, list_keep=list_keep, string_cap=string_cap)
            for item in value[:list_keep]
        ]
    if isinstance(value, str) and len(value) > string_cap:
        return value[:string_cap] + "…"
    return value


__all__ = [
    "TRACE_DETAIL_MAX_BYTES",
    "redact_trace_detail",
    "redact_trace_mapping",
    "redact_trace_payload",
]
