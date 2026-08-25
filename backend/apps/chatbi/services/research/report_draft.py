"""研究报告草案与部分报告生成（doc38 §10.3.5）。

服务端停止（预算耗尽、无新方向、取消、数据不足、执行失败）时生成结构化
部分报告；finish 成功时把结论草案固化为最终报告。两者都以 JSON 字符串
写入 ``ResearchRunSnapshot.report_draft`` / ``final_report``。
"""

from __future__ import annotations

from typing import Any

from apps.chatbi.models.dto.research_agent import (
    ResearchCompletion,
    ResearchHypothesisAssessment,
    ToolObservation,
)
from apps.chatbi.services.research.completion import evaluate_structural_coverage
from apps.chatbi.services.research.tool_context import ResearchToolContext

_MAX_LISTED_ITEMS = 20


def build_partial_report(
    ctx: ResearchToolContext,
    completion: ResearchCompletion,
    *,
    stop_reason: str,
) -> dict[str, Any]:
    """服务端强制停止时的受限报告：确认了什么、缺什么、为什么停。"""

    evaluation = evaluate_structural_coverage(
        ctx.requirement,
        ctx.analysis_evidences(),
        premise_result=ctx.premise_result,
    )
    failed = [
        observation
        for observation in ctx.observations()
        if observation.status.value != "succeeded"
    ]
    usage = ctx.budget_usage()
    budget = ctx.budget
    report: dict[str, Any] = {
        "kind": "partial",
        "run_id": ctx.run_id,
        "stop_reason": stop_reason,
        "status": completion.status,
        "reason": completion.reason.value,
        "summary": completion.summary,
        "confirmed": [
            {
                "evidence_id": item.evidence_id,
                "purpose": item.purpose[:200],
                "metric_refs": list(item.metric_refs),
                "dimension_refs": list(item.dimension_refs),
                "evidence_level": item.evidence_level.value,
            }
            for item in ctx.evidences()[:_MAX_LISTED_ITEMS]
        ],
        "unconfirmed": list(evaluation.gap_messages())[:_MAX_LISTED_ITEMS],
        "failed_queries": [
            {
                "tool_call_id": item.tool_call_id,
                "tool_name": item.tool_name,
                "error_code": (
                    item.error_code.value if item.error_code else None
                ),
                "message": (item.message or "")[:200],
            }
            for item in failed[:_MAX_LISTED_ITEMS]
        ],
        "not_executed": {
            "iterations_used": ctx.iteration,
            "max_iterations": budget.max_iterations,
            "queries_used": usage.queries,
            "model_calls_used": usage.model_calls,
        },
    }
    report["recommendation"] = _recommendation(
        stop_reason,
        bool(evaluation.missing_requirements),
    )
    return report


def build_final_report(
    ctx: ResearchToolContext,
    completion: ResearchCompletion,
    *,
    findings: list[Any] | tuple[Any, ...] = (),
    claims: list[Any] | tuple[Any, ...] = (),
    assessments: tuple[ResearchHypothesisAssessment, ...] = (),
) -> dict[str, Any]:
    """finish 通过校验后的结论草案：只包含已通过引用校验的内容。"""

    return {
        "kind": "final",
        "run_id": ctx.run_id,
        "status": completion.status,
        "reason": completion.reason.value,
        "summary": completion.summary,
        "claims": [_dump_contract(item) for item in claims][:_MAX_LISTED_ITEMS],
        "findings": [_dump_contract(item) for item in findings][
            :_MAX_LISTED_ITEMS
        ],
        "evidence_ids": list(completion.evidence_ids)[:_MAX_LISTED_ITEMS],
        "hypothesis_assessments": [
            _dump_contract(item) for item in assessments
        ][:_MAX_LISTED_ITEMS],
        "limitations": list(completion.limitations),
    }


def _dump_contract(item: Any) -> Any:
    """契约对象或已暂存的 JSON dict 统一转为 JSON 兼容载荷。"""

    return item if isinstance(item, dict) else item.model_dump(mode="json")


def failed_query_facts(observations: list[ToolObservation]) -> tuple[dict[str, Any], ...]:
    """失败观察的受控投影，供报告与时间线复用。"""

    return tuple(
        {
            "tool_call_id": item.tool_call_id,
            "tool_name": item.tool_name,
            "error_code": item.error_code.value if item.error_code else None,
        }
        for item in observations
        if item.status.value != "succeeded"
    )


def _recommendation(stop_reason: str, has_gaps: bool) -> str:
    if stop_reason == "cancelled":
        return "运行被取消；已确认的部分保留在证据台账中，可恢复后继续。"
    if stop_reason == "budget_exhausted":
        return (
            "研究因预算耗尽提前结束；建议缩小问题范围后重试，"
            "或基于已确认证据补充分析。"
        )
    if has_gaps:
        return "仍有未满足的证据需求；建议补充资产或缩小问题后重试。"
    return "研究按当前范围结束。"


__all__ = [
    "build_final_report",
    "build_partial_report",
    "failed_query_facts",
]
