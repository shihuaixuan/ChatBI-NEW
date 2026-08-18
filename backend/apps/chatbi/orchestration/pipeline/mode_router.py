"""三模式路由规则。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.chatbi.models.orm.agent_run import AgentExecutionMode
from apps.chatbi.services.planning.confidence import (
    ConfidenceAssessment,
    ConfidenceSignals,
    assess_confidence,
)


class ModeRoutingError(ValueError):
    """请求了未启用或不允许的执行模式。"""


@dataclass(frozen=True, slots=True)
class ModeRouteInput:
    """路由所需的已确认问题形态。"""

    enabled_modes: tuple[str, ...] = (
        AgentExecutionMode.FAST.value,
        AgentExecutionMode.PLAN.value,
    )
    category: str = "data_query"
    query_shape: dict[str, Any] = field(default_factory=dict)
    intent_type: str | None = None
    requested_mode: str | None = None
    multi_query: bool = False
    cross_model: bool = False
    dataset_mode: str | None = None


class ModeRouter:
    """按问题形态选择 P1 执行模式，不回退到 ReAct。"""

    def route(self, request: ModeRouteInput) -> AgentExecutionMode:
        enabled = _normalize_modes(request.enabled_modes)
        requested = _normalize_mode(request.requested_mode)
        if requested is not None:
            if requested not in enabled:
                raise ModeRoutingError(f"EXECUTION_MODE_NOT_ENABLED:{requested}")
            return AgentExecutionMode(requested)

        if request.category != "data_query":
            # meta_query 和 out_of_scope 通常在准备阶段结束；chitchat 仍由现有直答收口。
            return AgentExecutionMode.REACT_LEGACY
        if request.cross_model or request.multi_query:
            return self._first_enabled(
                enabled,
                (AgentExecutionMode.PLAN.value,),
            )
        if request.intent_type in {"share_analysis", "composition"}:
            return self._first_enabled(
                enabled,
                (AgentExecutionMode.PLAN.value,),
            )
        if _requires_research(request.query_shape):
            return self._first_enabled(
                enabled,
                (AgentExecutionMode.RESEARCH.value, AgentExecutionMode.PLAN.value),
            )
        if _is_fast_shape(request.query_shape) and AgentExecutionMode.FAST.value in enabled:
            return AgentExecutionMode.FAST
        if _is_complex_shape(request.query_shape):
            return self._first_enabled(
                enabled,
                (AgentExecutionMode.PLAN.value,),
            )
        return self._first_enabled(
            enabled,
            (AgentExecutionMode.FAST.value, AgentExecutionMode.PLAN.value),
        )

    @staticmethod
    def assess_confidence(signals: ConfidenceSignals) -> ConfidenceAssessment:
        """公开统一置信度入口，模式选择和回答阶段使用同一规则。"""

        return assess_confidence(signals)

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
    try:
        return AgentExecutionMode(normalized).value
    except ValueError as exc:
        raise ModeRoutingError(f"EXECUTION_MODE_INVALID:{normalized}") from exc


def _is_fast_shape(shape: dict[str, Any]) -> bool:
    """FAST 只接受单查询、无跨结果集计算的形态。"""

    return not any(
        bool(shape.get(key))
        for key in (
            "multi_query",
            "cross_query",
            "comparison",
            "comparison_type",
            "needs_compute",
            "needs_attribution",
            "research",
            "share_analysis",
            "composition",
        )
    )


def _is_complex_shape(shape: dict[str, Any]) -> bool:
    return bool(
        shape.get("multi_query")
        or shape.get("cross_query")
        or shape.get("comparison")
        or shape.get("comparison_type")
        or shape.get("needs_compute")
        or shape.get("needs_attribution")
    )


def _requires_research(shape: dict[str, Any]) -> bool:
    return bool(shape.get("research") or shape.get("analysis_depth") == "research")


__all__ = ["ModeRouteInput", "ModeRouter", "ModeRoutingError"]
