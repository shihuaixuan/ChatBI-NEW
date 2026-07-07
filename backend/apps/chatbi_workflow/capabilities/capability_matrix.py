from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CapabilityDecision:
    """查询计划能力矩阵的路由结果。"""

    status: str
    strategy: str
    reason_code: str | None = None
    reason: str | None = None


_SEMANTIC_COMPILER_INTENTS = {
    "",
    "unknown",
    "metric_query",
    "trend_analysis",
    "ranking_analysis",
    "detail_query",
}
_MULTI_QUERY_INTENTS = {"comparison_analysis", "share_analysis"}


def decide_capability(intent: dict[str, Any], plan_features: dict[str, Any]) -> CapabilityDecision:
    """按意图与计划特征决定处理通道，禁止不可表达问题静默降级。"""

    intent_type = str(intent.get("intent_type") or "").strip().lower()
    sub_plans = plan_features.get("sub_plans")
    if intent_type in _SEMANTIC_COMPILER_INTENTS:
        return CapabilityDecision(status="ready", strategy="semantic_compiler")
    if intent_type in _MULTI_QUERY_INTENTS:
        if isinstance(sub_plans, list) and len(sub_plans) >= 2:
            return CapabilityDecision(status="ready", strategy="multi_query")
        return CapabilityDecision(
            status="infeasible",
            strategy="infeasible",
            reason_code="multi_query_plan_required",
            reason="该分析类型需要多查询计划，当前计划尚未形成可执行子查询",
        )
    return CapabilityDecision(
        status="infeasible",
        strategy="infeasible",
        reason_code="unsupported_intent_type",
        reason=f"暂不支持 {intent_type or 'unknown'} 类型的确定性 SQL 计划",
    )
