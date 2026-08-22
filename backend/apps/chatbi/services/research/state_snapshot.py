"""Research Run 快照构建与 Evidence DAG 校验（阶段 4）。

本模块是 ``state["research_state"]`` 与持久化层之间的投影边界：

- ``validate_evidence_dag``：对整个证据台账做统一 DAG 校验（存在、同 Run、
  无自引用、迭代先序、无环），登记新证据和恢复旧状态时都必须通过；
- ``build_research_run_snapshot``：把 Context 中的研究事实投影成
  ``ResearchRunSnapshot``（§8.4.1 字段全集），只保存受控摘要；
- ``ensure_snapshot_size``：derived_state 只保存摘要的硬性尺寸守卫。

规范事实源仍是 ``research_state``（它保留全部成功观察以支撑重放）；
快照是其审计视图，两者一起写入 ``run.derived_state``。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from apps.chatbi.models.dto.research_agent import (
    ResearchBudgetRemaining,
    ResearchEvidence,
    ResearchEvidenceEdge,
    ResearchRunSnapshot,
    ResearchRunStatus,
    ToolObservationStatus,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from apps.chatbi.services.research.tool_context import ResearchToolContext

MAX_SNAPSHOT_JSON_CHARS = 400_000


def validate_evidence_dag(
    evidences: Sequence[ResearchEvidence],
    *,
    run_id: str | None = None,
) -> None:
    """统一校验证据台账：引用完整、归属当前 Run、无自引用、无环。"""

    by_id: dict[str, ResearchEvidence] = {}
    for item in evidences:
        if item.evidence_id in by_id:
            raise ValueError("RESEARCH_AGENT_STATE_EVIDENCE_DUPLICATED")
        by_id[item.evidence_id] = item
    for item in evidences:
        if run_id is not None and item.run_id != run_id:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
        for dependency in item.dependencies:
            source = by_id.get(dependency.evidence_id)
            if source is None:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
            if dependency.run_id != item.run_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
            if dependency.evidence_id == item.evidence_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_SELF_DEPENDENCY")
            if source.iteration != dependency.source_iteration:
                raise ValueError(
                    "RESEARCH_AGENT_EVIDENCE_DEPENDENCY_ITERATION_MISMATCH"
                )
    # Kahn 拓扑校验。契约层的“依赖迭代必须先于派生”已经从结构上排除了环，
    # 这里对恢复加载的不可信状态再做一次防御性检查。
    indegree = dict.fromkeys(by_id, 0)
    dependents: dict[str, list[str]] = {evidence_id: [] for evidence_id in by_id}
    for item in evidences:
        for dependency in item.dependencies:
            indegree[item.evidence_id] += 1
            dependents[dependency.evidence_id].append(item.evidence_id)
    frontier = [evidence_id for evidence_id, degree in indegree.items() if degree == 0]
    visited = 0
    while frontier:
        node = frontier.pop()
        visited += 1
        for follower in dependents[node]:
            indegree[follower] -= 1
            if indegree[follower] == 0:
                frontier.append(follower)
    if visited != len(by_id):
        raise ValueError("RESEARCH_AGENT_EVIDENCE_DEPENDENCY_CYCLE")


def build_research_run_snapshot(
    ctx: ResearchToolContext,
    *,
    running_tool_call_ids: Collection[str] = (),
    agent_run_id: int | None = None,
    report_draft: str | None = None,
    final_report: str | None = None,
    premise_result: dict[str, Any] | None = None,
) -> ResearchRunSnapshot:
    """把 Context 中的研究事实投影成可持久化快照。"""

    requirement = ctx.requirement
    budget = ctx.budget
    usage = ctx.budget_usage()
    iteration = ctx.iteration
    observations = ctx.observations()
    completion = ctx.completion
    evidences = ctx.evidences()

    validate_evidence_dag(evidences, run_id=requirement.run_id)
    if completion is not None:
        completion.validate_evidence(evidences)

    if completion is not None:
        status = ResearchRunStatus(completion.status)
        finish_reason = completion.reason
    elif iteration > 0 or observations or evidences:
        status = ResearchRunStatus.RUNNING
        finish_reason = None
    else:
        status = ResearchRunStatus.INITIALIZING
        finish_reason = None

    edges = [
        ResearchEvidenceEdge(
            evidence_id=item.evidence_id,
            depends_on=dependency.evidence_id,
            relation=dependency.relation,
            source_iteration=dependency.source_iteration,
        )
        for item in evidences
        for dependency in item.dependencies
    ]
    assessments = ctx.hypothesis_assessments()
    hypothesis_ids: list[str] = []
    for item in evidences:
        for hypothesis_id in item.hypothesis_ids:
            if hypothesis_id not in hypothesis_ids:
                hypothesis_ids.append(hypothesis_id)
    for assessment in assessments:
        if assessment.hypothesis_id not in hypothesis_ids:
            hypothesis_ids.append(assessment.hypothesis_id)

    snapshot = ResearchRunSnapshot(
        run_id=requirement.run_id,
        goal=requirement.goal,
        status=status,
        finish_reason=finish_reason,
        premise_result=premise_result,
        agent_run_id=agent_run_id,
        iteration=iteration,
        version_snapshot=requirement.version_snapshot,
        scope_fingerprint=requirement.scope.scope_fingerprint,
        budget=budget,
        budget_usage=usage,
        budget_remaining=ResearchBudgetRemaining(
            iterations=max(budget.max_iterations - iteration, 0),
            queries=max(budget.max_queries - usage.queries, 0),
            model_calls=max(budget.max_model_calls - usage.model_calls, 0),
            duration_seconds=float(
                max(budget.max_duration_seconds - usage.duration_seconds, 0)
            ),
        ),
        evidences=tuple(evidences),
        dependency_edges=tuple(edges),
        hypothesis_ids=tuple(hypothesis_ids),
        hypothesis_assessments=assessments,
        completed_tool_call_ids=tuple(
            observation.tool_call_id for observation in observations
        ),
        running_tool_call_ids=tuple(dict.fromkeys(running_tool_call_ids)),
        failed_observations=tuple(
            observation
            for observation in observations
            if observation.status is not ToolObservationStatus.SUCCEEDED
        ),
        report_draft=report_draft,
        final_report=final_report,
    )
    ensure_snapshot_size(snapshot)
    return snapshot


def ensure_snapshot_size(snapshot: ResearchRunSnapshot) -> int:
    """derived_state 只保存受控摘要；超限直接拒绝持久化。"""

    encoded_chars = len(
        json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)
    )
    if encoded_chars > MAX_SNAPSHOT_JSON_CHARS:
        raise ValueError("RESEARCH_SNAPSHOT_TOO_LARGE")
    return encoded_chars


__all__ = [
    "MAX_SNAPSHOT_JSON_CHARS",
    "ResearchRunSnapshot",
    "build_research_run_snapshot",
    "ensure_snapshot_size",
    "validate_evidence_dag",
]
