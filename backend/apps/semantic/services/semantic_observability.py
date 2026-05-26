import re
from typing import Any

from common.utils.utils import SQLBotLogUtil

SEMANTIC_METRIC_NAMES = {
    "semantic_candidate_init_total",
    "semantic_asset_approved_total",
    "semantic_search_latency_ms",
    "semantic_search_empty_total",
    "semantic_embedding_failed_total",
    "semantic_expr_validate_failed_total",
    "semantic_prompt_injected_total",
}

_semantic_metric_counts: dict[str, int] = dict.fromkeys(SEMANTIC_METRIC_NAMES, 0)
_sensitive_assignment = re.compile(r"\b(password|token|configuration)\s*=\s*\S+", re.IGNORECASE)
_connection_url = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)


def reset_semantic_metric_counts() -> None:
    for name in SEMANTIC_METRIC_NAMES:
        _semantic_metric_counts[name] = 0


def get_semantic_metric_count(name: str) -> int:
    return _semantic_metric_counts.get(name, 0)


def _sanitize_value(value: Any) -> str:
    text = str(value)
    text = _connection_url.sub("[REDACTED_DSN]", text)
    return _sensitive_assignment.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)


def record_semantic_metric(name: str, increment: int = 1, **fields: Any) -> None:
    if name not in SEMANTIC_METRIC_NAMES:
        return
    _semantic_metric_counts[name] = _semantic_metric_counts.get(name, 0) + increment
    field_text = " ".join(f"{key}={_sanitize_value(value)}" for key, value in fields.items() if value is not None)
    SQLBotLogUtil.info(f"semantic_metric name={name} count={_semantic_metric_counts[name]} {field_text}".rstrip())
