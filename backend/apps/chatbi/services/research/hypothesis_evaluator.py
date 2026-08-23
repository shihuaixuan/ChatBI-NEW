"""Hypothesis Evaluator：模型假设评估的服务端裁决（doc38 §10.3.1）。

模型经 ``finish_research`` 提交的每个假设评估都要经过本模块：

- SUPPORTED / WEAKENED 必须引用证据，证据必须属于当前 Run；
- INVALID 只能由服务端治理校验产生，模型不能自行提交；
- 引用的证据被截断或没有样本行时强制降级为 INCONCLUSIVE；
- 确定性对账结论优先：服务端可以用 :func:`apply_deterministic_status`
  覆盖模型的判断，模型不能覆盖服务端。

全部裁决留下审计记录（请求值 → 最终值、是否降级、原因），随研究状态
持久化供快照和报告使用。旧 ``hypotheses.py`` 中依赖
``MaterializedResearchAction`` 的确定性公式逻辑不迁移；它随阶段 8 的旧
架构删除。
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.chatbi.models.dto.research_agent import (
    ResearchEvidence,
    ResearchHypothesisAssessment,
)


@dataclass(frozen=True)
class HypothesisAuditRecord:
    """一条假设评估的服务端裁决记录。"""

    hypothesis_id: str
    requested: str
    final: str
    downgraded: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class HypothesisEvaluation:
    """裁决结果：修正后的评估列表 + 审计记录 + 拒绝原因。"""

    assessments: tuple[ResearchHypothesisAssessment, ...]
    audit: tuple[HypothesisAuditRecord, ...] = ()
    violations: tuple[str, ...] = ()


class HypothesisEvaluationError(ValueError):
    """模型评估违反状态机规则；携带稳定错误码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def evaluate_hypothesis_assessments(
    assessments: list[ResearchHypothesisAssessment]
    | tuple[ResearchHypothesisAssessment, ...],
    evidences: list[ResearchEvidence] | tuple[ResearchEvidence, ...],
) -> HypothesisEvaluation:
    """对提交的假设评估逐条裁决；有违规时整体拒绝。"""

    evidence_by_id = {item.evidence_id: item for item in evidences}
    audit: list[HypothesisAuditRecord] = []
    violations: list[str] = []
    finals: list[ResearchHypothesisAssessment] = []
    for assessment in assessments:
        missing = [
            item for item in assessment.evidence_ids if item not in evidence_by_id
        ]
        if missing:
            raise HypothesisEvaluationError(
                "RESEARCH_AGENT_EVIDENCE_NOT_FOUND",
                f"假设 {assessment.hypothesis_id} 引用了不存在的证据："
                f"{', '.join(missing)}。",
            )
        if assessment.assessment == "invalid":
            violations.append(
                f"假设 {assessment.hypothesis_id}：invalid 只能由服务端判定，"
                "模型不能提交。"
            )
            continue
        if assessment.assessment in {"supported", "weakened"} and (
            not assessment.evidence_ids
        ):
            raise HypothesisEvaluationError(
                "RESEARCH_AGENT_HYPOTHESIS_EVIDENCE_REQUIRED",
                f"假设 {assessment.hypothesis_id} 判定为 "
                f"{assessment.assessment} 必须引用至少一条证据。",
            )
        final_assessment = assessment
        record = HypothesisAuditRecord(
            hypothesis_id=assessment.hypothesis_id,
            requested=assessment.assessment,
            final=assessment.assessment,
        )
        if assessment.evidence_ids and _insufficient(evidence_by_id, assessment):
            final_assessment = assessment.model_copy(update={"assessment": "inconclusive"})
            record = HypothesisAuditRecord(
                hypothesis_id=assessment.hypothesis_id,
                requested=assessment.assessment,
                final="inconclusive",
                downgraded=True,
                reason="引用证据被截断或没有样本行，服务端降级为无法判断。",
            )
        audit.append(record)
        finals.append(final_assessment)
    return HypothesisEvaluation(
        assessments=tuple(finals),
        audit=tuple(audit),
        violations=tuple(violations),
    )


def apply_deterministic_status(
    assessments: tuple[ResearchHypothesisAssessment, ...],
    *,
    hypothesis_id: str,
    status: str,
    evidence_id: str,
    reason: str | None = None,
) -> tuple[ResearchHypothesisAssessment, ...]:
    """确定性验证结果覆盖模型评估（服务端优先，模型不能反向覆盖）。"""

    updated: list[ResearchHypothesisAssessment] = []
    found = False
    for item in assessments:
        if item.hypothesis_id != hypothesis_id:
            updated.append(item)
            continue
        found = True
        updated.append(
            item.model_copy(
                update={
                    "assessment": status,
                    "evidence_ids": tuple(
                        dict.fromkeys((*item.evidence_ids, evidence_id))
                    ),
                    "reason": reason or item.reason,
                }
            )
        )
    if not found:
        raise HypothesisEvaluationError(
            "RESEARCH_AGENT_HYPOTHESIS_ID_INVALID",
            f"确定性结果对应的假设不存在：{hypothesis_id}",
        )
    return tuple(updated)


def _insufficient(
    evidence_by_id: dict[str, ResearchEvidence],
    assessment: ResearchHypothesisAssessment,
) -> bool:
    return any(
        evidence_by_id[item].statistics.truncated
        or evidence_by_id[item].statistics.row_count == 0
        for item in assessment.evidence_ids
    )


__all__ = [
    "HypothesisAuditRecord",
    "HypothesisEvaluation",
    "HypothesisEvaluationError",
    "apply_deterministic_status",
    "evaluate_hypothesis_assessments",
]
