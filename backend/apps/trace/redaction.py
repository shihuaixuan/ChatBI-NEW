"""Trace 输入输出的统一脱敏规则。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

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


__all__ = ["redact_trace_mapping", "redact_trace_payload"]
