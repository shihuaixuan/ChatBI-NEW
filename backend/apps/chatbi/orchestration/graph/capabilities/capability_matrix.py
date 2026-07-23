from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CapabilityDecision:
    """查询计划能力矩阵的路由结果。

    downgrade：意图倾向多查询，但计划要素不全（缺时间窗/维度等），无法形成
    多查询子计划。此时不判 infeasible（那是"问题本身不可表达"），而是回退到
    单查询语义编译器 + 一条说明，避免"反静默降级"用力过猛把可回答的高频问句
    （维度未绑的占比、relative_range 的环比）误报成能力缺失。
    """

    status: str
    strategy: str
    reason_code: str | None = None
    reason: str | None = None
    downgrade_note: str | None = None


_SEMANTIC_COMPILER_INTENTS = {
    "",
    "unknown",
    "metric_query",
    "trend_analysis",
    "ranking_analysis",
    "detail_query",
}
_MULTI_QUERY_INTENTS = {"comparison_analysis", "share_analysis"}
_MULTI_QUERY_DOWNGRADE_NOTES = {
    "comparison_analysis": "未能自动构建对比时间窗，已按单次聚合返回，可明确起止时间后再对比",
    "share_analysis": "未能自动构建占比子查询（需分组维度），已按单次聚合返回",
}


def decide_capability(intent: dict[str, Any], plan_features: dict[str, Any]) -> CapabilityDecision:
    """按意图与计划特征决定处理通道，禁止不可表达问题静默降级。"""

    intent_type = str(intent.get("intent_type") or "").strip().lower()
    sub_plans = plan_features.get("sub_plans")
    if intent_type in _SEMANTIC_COMPILER_INTENTS:
        return CapabilityDecision(status="ready", strategy="semantic_compiler")
    if intent_type in _MULTI_QUERY_INTENTS:
        if isinstance(sub_plans, list) and len(sub_plans) >= 2:
            return CapabilityDecision(status="ready", strategy="multi_query")
        # 要素不全 ≠ 不可表达：回退单查询并如实说明，而不是笼统 infeasible。
        return CapabilityDecision(
            status="ready",
            strategy="semantic_compiler",
            reason_code="multi_query_downgraded_to_single",
            reason="多查询要素不全，回退单次聚合查询",
            downgrade_note=_MULTI_QUERY_DOWNGRADE_NOTES.get(
                intent_type, "未能构建多查询计划，已按单次聚合返回"
            ),
        )
    return CapabilityDecision(
        status="infeasible",
        strategy="infeasible",
        reason_code="unsupported_intent_type",
        reason=f"暂不支持 {intent_type or 'unknown'} 类型的确定性 SQL 计划",
    )
