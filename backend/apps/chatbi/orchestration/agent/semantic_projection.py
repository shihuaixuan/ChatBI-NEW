"""Agent 并发工具结果的语义计划投影。"""

from __future__ import annotations

from typing import Any


def refresh_semantic_projection(
    state: dict[str, Any],
    raw_package: dict[str, Any],
    raw_scope: dict[str, Any],
    *,
    schema_data: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """候选资产阶段不回写时间结果和查询计划。"""

    _ = state, schema_data
    return raw_package, raw_scope, {}


__all__ = ["refresh_semantic_projection"]
