"""语义检索结果的表权限过滤。"""

from __future__ import annotations

from typing import Any, cast


def filter_semantic_payload_tables(
    payload: dict[str, Any],
    authorized_tables: list[str] | set[str],
) -> dict[str, Any]:
    """过滤语义结果中明确标注了未授权物理表的内容。"""

    allowed = {str(table).lower() for table in authorized_tables}
    filtered = cast(dict[str, Any], _filter_nested_assets(payload, allowed))
    filtered["tables"] = [
        table
        for table in payload.get("tables") or []
        if str(table).lower() in allowed
    ]
    return filtered


def _filter_nested_assets(value: Any, allowed: set[str]) -> Any:
    if isinstance(value, list):
        return [
            _filter_nested_assets(item, allowed)
            for item in value
            if not _has_unauthorized_table(item, allowed)
        ]
    if isinstance(value, dict):
        return {
            key: _filter_nested_assets(item, allowed)
            for key, item in value.items()
            if not _has_unauthorized_table(item, allowed)
        }
    return value


def _has_unauthorized_table(value: Any, allowed: set[str]) -> bool:
    if not isinstance(value, dict):
        return False
    table = next(
        (
            value.get(key)
            for key in ("table", "table_name", "physical_table")
            if value.get(key)
        ),
        None,
    )
    return table is not None and str(table).lower() not in allowed


__all__ = ["filter_semantic_payload_tables"]
