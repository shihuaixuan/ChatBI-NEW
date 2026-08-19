"""基于语义解析结果的 Fast、Plan 模型路由。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chatbi.models.dto.semantic_parse import SemanticParseOutput
from apps.chatbi.models.orm.agent_run import AgentExecutionMode


class ModeRoutingError(ValueError):
    """请求了未启用或不允许的执行模式。"""


@dataclass(frozen=True, slots=True)
class ModeRouteInput:
    """模型路由所需的语义解析结果和候选资产。"""

    semantic_parse: SemanticParseOutput
    candidate_groups: dict[str, list[dict[str, Any]]]
    enabled_modes: tuple[str, ...] = (
        AgentExecutionMode.FAST.value,
        AgentExecutionMode.PLAN.value,
    )
    requested_mode: str | None = None


class ModeRouter:
    """根据语义解析结果选择 Fast 或 Plan，不重新理解问题。"""

    def route(self, request: ModeRouteInput) -> AgentExecutionMode:
        enabled = _normalize_modes(request.enabled_modes)
        automatic_mode = self._route_semantic_parse(request)
        requested = _normalize_mode(request.requested_mode)
        if requested is not None:
            if requested not in enabled:
                raise ModeRoutingError(f"EXECUTION_MODE_NOT_ENABLED:{requested}")
            return AgentExecutionMode(requested)

        return self._first_enabled(enabled, (automatic_mode.value,))

    @classmethod
    def _route_semantic_parse(cls, request: ModeRouteInput) -> AgentExecutionMode:
        """根据已校验语义解析结果选择模型路由。"""

        semantic_parse = request.semantic_parse
        if semantic_parse.status != "resolved" or semantic_parse.unresolved:
            raise ModeRoutingError("SEMANTIC_PARSE_NOT_RESOLVED")

        candidates = {
            str(item.get("ref")): item
            for group in request.candidate_groups.values()
            for item in group
            if isinstance(item, dict) and item.get("ref")
        }
        selected_refs = [
            *(item.ref for item in semantic_parse.measures),
            *(item.ref for item in semantic_parse.group_by),
            *(item.target_ref for item in semantic_parse.filters),
            *(item.target_ref for item in semantic_parse.order_by),
        ]
        missing_refs = sorted(set(selected_refs) - set(candidates))
        if missing_refs:
            raise ModeRoutingError(
                "SEMANTIC_PARSE_CANDIDATE_NOT_FOUND:" + ",".join(missing_refs)
            )

        missing_model_refs = sorted(
            ref for ref in set(selected_refs) if candidates[ref].get("model_id") is None
        )
        if missing_model_refs:
            raise ModeRoutingError(
                "SEMANTIC_PARSE_CANDIDATE_MODEL_REQUIRED:"
                + ",".join(missing_model_refs)
            )

        model_ids = {int(candidates[ref]["model_id"]) for ref in selected_refs}
        if len(model_ids) > 1:
            return AgentExecutionMode.PLAN
        if len(semantic_parse.time_filters) > 1:
            return AgentExecutionMode.PLAN
        if semantic_parse.calculations:
            return AgentExecutionMode.PLAN
        return AgentExecutionMode.FAST

    @staticmethod
    def _first_enabled(enabled: set[str], candidates: tuple[str, ...]) -> AgentExecutionMode:
        for candidate in candidates:
            if candidate in enabled:
                return AgentExecutionMode(candidate)
        raise ModeRoutingError("EXECUTION_MODE_NOT_AVAILABLE")


def _normalize_modes(modes: tuple[str, ...] | list[str] | str) -> set[str]:
    values = modes.split(",") if isinstance(modes, str) else modes
    normalized = {_normalize_mode(item) for item in values}
    return {item for item in normalized if item is not None}


def _normalize_mode(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized not in {
        AgentExecutionMode.FAST.value,
        AgentExecutionMode.PLAN.value,
    }:
        raise ModeRoutingError(f"EXECUTION_MODE_INVALID:{normalized}")
    return normalized


__all__ = ["ModeRouteInput", "ModeRouter", "ModeRoutingError"]
