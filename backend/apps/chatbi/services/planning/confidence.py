"""P1-8 置信度四档路由的确定性规则。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ConfidenceRoute = Literal["direct", "disclose", "clarify", "reject"]


@dataclass(frozen=True, slots=True)
class ConfidenceSignals:
    """置信度计算所需的服务端信号。"""

    binding_confidence: float = 0.0
    evidence_level: str = "none"
    validation_status: str = "unknown"
    verified_hit: bool = False
    fallback_channel: bool = False
    semantic_enforcement: str = "LEGACY"
    ambiguous: bool = False
    out_of_scope: bool = False


@dataclass(frozen=True, slots=True)
class ConfidenceAssessment:
    """四档路由结果及可展示的判定依据。"""

    route: ConfidenceRoute
    score: float
    reasons: tuple[str, ...] = ()
    certified: bool = False
    fallback_allowed: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)


def assess_confidence(signals: ConfidenceSignals) -> ConfidenceAssessment:
    """按绑定证据、计划校验、认证命中和通道计算路由。

    规则优先于模型分数：越权、明确歧义和未通过校验不能被高分覆盖。
    """

    score = max(0.0, min(1.0, float(signals.binding_confidence)))
    reasons: list[str] = []
    evidence = {
        "binding_confidence": score,
        "evidence_level": signals.evidence_level,
        "validation_status": signals.validation_status,
        "verified_hit": signals.verified_hit,
        "fallback_channel": signals.fallback_channel,
        "semantic_enforcement": signals.semantic_enforcement,
    }
    if signals.out_of_scope:
        return ConfidenceAssessment(
            route="reject",
            score=score,
            reasons=("out_of_scope",),
            evidence=evidence,
        )
    if signals.ambiguous:
        return ConfidenceAssessment(
            route="clarify",
            score=score,
            reasons=("binding_ambiguous",),
            evidence=evidence,
        )
    if signals.validation_status.lower() in {"invalid", "rejected", "failed"}:
        reasons.append("plan_validation_failed")
        route: ConfidenceRoute = "reject"
    elif signals.validation_status.lower() not in {"proven", "valid", "succeeded"}:
        reasons.append("plan_not_proven")
        route = "clarify"
    elif score < 0.65:
        reasons.append("binding_confidence_low")
        route = "reject"
    elif score < 0.85 or signals.evidence_level not in {"exact", "alias"}:
        reasons.append("evidence_requires_disclosure")
        route = "disclose"
    else:
        route = "direct"

    certified = bool(
        route in {"direct", "disclose"}
        and signals.verified_hit
        and not signals.fallback_channel
        and signals.semantic_enforcement.upper() == "STRICT"
    )
    fallback_allowed = bool(
        signals.semantic_enforcement.upper() == "ASSISTED"
        and not signals.out_of_scope
        and route in {"reject", "disclose"}
    )
    return ConfidenceAssessment(
        route=route,
        score=score,
        reasons=tuple(reasons),
        certified=certified,
        fallback_allowed=fallback_allowed,
        evidence=evidence,
    )


__all__ = [
    "ConfidenceAssessment",
    "ConfidenceRoute",
    "ConfidenceSignals",
    "assess_confidence",
]
