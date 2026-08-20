"""Research 第二阶段：受控模型决策与已证明子计划的最小动态闭环。"""

from __future__ import annotations

import time
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from typing import Any

from apps.chatbi.errors import ResearchExecutionError
from apps.chatbi.models.dto.execution_requirement import (
    ExecutionRequirement,
    execution_requirement_from_state,
)
from apps.chatbi.models.dto.research import (
    EvidenceSnapshot,
    ResearchAction,
    ResearchFinishReason,
    ResearchRemainingBudget,
    ResearchRequirement,
    ResearchRunStatus,
    ResearchState,
    ResearchTerminationReason,
)
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.pipeline.plan_mode import (
    PlanExecutionOutcome,
    PlanPipeline,
    PlanPipelineError,
)
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.research.actions import (
    MaterializedResearchAction,
    initial_compare_action,
    materialize_research_action,
)
from apps.chatbi.services.research.evidence import (
    premise_supported,
    project_evidence_snapshot,
)
from apps.chatbi.services.research.policy import ResearchPolicy
from apps.event import RenderEvent


class ResearchPipelineError(RuntimeError):
    """Research 无法继续形成可信动态闭环。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True, slots=True)
class ResearchPipelineDependencies:
    policy: ResearchPolicy
    plan_pipeline: PlanPipeline
    lifecycle: AgentLifecycle
    session: Any


class ResearchPipeline:
    """控制预算、去重、子计划执行、证据追加和停止条件。"""

    def __init__(self, dependencies: ResearchPipelineDependencies) -> None:
        if dependencies.policy is None:
            raise ValueError("RESEARCH_POLICY_REQUIRED")
        if dependencies.plan_pipeline is None:
            raise ValueError("RESEARCH_PLAN_PIPELINE_REQUIRED")
        self._policy = dependencies.policy
        self._plan_pipeline = dependencies.plan_pipeline
        self._lifecycle = dependencies.lifecycle
        self._session = dependencies.session

    def run(self, state: AgentRuntimeState) -> Iterator[RenderEvent]:
        root_requirement = self._load_requirement(state)
        research = root_requirement.research_requirement
        if research is None:
            raise ResearchPipelineError("RESEARCH_REQUIREMENT_REQUIRED")
        started_at = time.monotonic()
        research_id = f"research-{state.require_run_id()}"
        current_state = ResearchState(
            research_id=research_id,
            goal=research.goal,
            status=ResearchRunStatus.RUNNING,
            remaining_budget=ResearchRemainingBudget(
                iterations=research.budget.max_iterations,
                queries=research.budget.max_queries,
                model_calls=research.budget.max_model_calls,
                duration_seconds=research.budget.max_duration_seconds,
            ),
        )
        evidence: list[EvidenceSnapshot] = []
        evidence_by_result: dict[str, EvidenceSnapshot] = {}
        last_outcome: PlanExecutionOutcome | None = None
        last_summary = ""
        self._persist(state, current_state, evidence)

        # 第一次查询由规则生成，用于确认用户描述的变化方向是否成立。
        initial = initial_compare_action(research)
        materialized = self._materialize(
            initial,
            research,
            root_requirement,
            evidence_by_result,
        )
        current_state = self._reserve_queries(
            current_state,
            len(materialized.requirement.query_requirements),
            started_at,
            research,
        )
        outcome = yield from self._execute_action(
            state,
            materialized,
            plan_id=f"plan:{research_id}:premise",
        )
        if outcome is None:
            return
        last_outcome = outcome
        initial_evidence = self._project_evidence(
            materialized,
            outcome,
            evidence_index=1,
            research=research,
        )
        evidence.append(initial_evidence)
        evidence_by_result[initial_evidence.result_id] = initial_evidence
        current_state = current_state.model_copy(
            update={
                "evidence_ids": (initial_evidence.evidence_id,),
                "executed_action_fingerprints": (materialized.fingerprint,),
                "remaining_budget": self._remaining_duration(
                    current_state.remaining_budget,
                    started_at,
                    research,
                ),
            }
        )
        self._persist(state, current_state, evidence)
        premise = premise_supported(research.goal, initial_evidence)
        if premise is False:
            current_state = self._terminal_state(
                current_state,
                status=ResearchRunStatus.SUCCEEDED,
                reason=ResearchTerminationReason.PREMISE_NOT_SUPPORTED,
            )
            last_summary = "实际数据不支持问题中声明的变化方向，研究已在现象确认后结束。"
            self._persist(state, current_state, evidence)
            yield from self._finish(
                state,
                current_state,
                evidence,
                last_summary,
                last_outcome,
            )
            return

        while current_state.iteration < research.budget.max_iterations:
            current_state = self._remaining_state(
                current_state,
                started_at,
                research,
            )
            if self._budget_exhausted(current_state):
                current_state = self._budget_terminal(current_state, bool(evidence))
                last_summary = "Research 已达到服务端预算上限。"
                break
            # 模型调用在发起前扣减，失败或输出非法同样消耗预算。
            current_state = current_state.model_copy(
                update={
                    "iteration": current_state.iteration + 1,
                    "remaining_budget": current_state.remaining_budget.model_copy(
                        update={
                            "iterations": current_state.remaining_budget.iterations - 1,
                            "model_calls": current_state.remaining_budget.model_calls - 1,
                        }
                    ),
                }
            )
            self._persist(state, current_state, evidence)
            try:
                decision = self._policy.decide(
                    requirement=research,
                    state=current_state,
                    evidence=tuple(evidence),
                    asset_catalog=self._asset_catalog(root_requirement),
                )
            except ResearchExecutionError as exc:
                raise ResearchPipelineError(exc.code) from exc
            last_summary = decision.assessment.evidence_summary
            if decision.decision.type == "finish":
                status, reason = _finish_state(decision.decision.reason)
                current_state = self._terminal_state(
                    current_state,
                    status=status,
                    reason=reason,
                )
                break

            materialized_actions: list[MaterializedResearchAction] = []
            pending_fingerprints = set(current_state.executed_action_fingerprints)
            for action in decision.decision.actions:
                item = self._materialize(
                    action,
                    research,
                    root_requirement,
                    evidence_by_result,
                )
                if item.fingerprint in pending_fingerprints:
                    raise ResearchPipelineError(
                        ResearchExecutionError.ACTION_DUPLICATED
                    )
                pending_fingerprints.add(item.fingerprint)
                materialized_actions.append(item)
            required_queries = sum(
                len(item.requirement.query_requirements)
                for item in materialized_actions
            )
            current_state = self._reserve_queries(
                current_state,
                required_queries,
                started_at,
                research,
            )
            new_evidence_count = 0
            for action_index, item in enumerate(materialized_actions, start=1):
                outcome = yield from self._execute_action(
                    state,
                    item,
                    plan_id=(
                        f"plan:{research_id}:{current_state.iteration}:{action_index}"
                    ),
                )
                if outcome is None:
                    return
                last_outcome = outcome
                snapshot = self._project_evidence(
                    item,
                    outcome,
                    evidence_index=len(evidence) + 1,
                    research=research,
                )
                evidence.append(snapshot)
                evidence_by_result[snapshot.result_id] = snapshot
                if snapshot.statistics.row_count > 0:
                    new_evidence_count += 1
            current_state = current_state.model_copy(
                update={
                    "evidence_ids": tuple(item.evidence_id for item in evidence),
                    "executed_action_fingerprints": tuple(sorted(pending_fingerprints)),
                    "remaining_budget": self._remaining_duration(
                        current_state.remaining_budget,
                        started_at,
                        research,
                    ),
                }
            )
            self._persist(state, current_state, evidence)
            if new_evidence_count == 0:
                current_state = self._terminal_state(
                    current_state,
                    status=ResearchRunStatus.FAILED,
                    reason=ResearchTerminationReason.DATA_INSUFFICIENT,
                )
                last_summary = "本轮没有获得可继续分析的数据。"
                break
        else:
            current_state = self._budget_terminal(current_state, bool(evidence))
            last_summary = "Research 已达到最大迭代次数。"

        if current_state.finish_reason is None:
            current_state = self._budget_terminal(current_state, bool(evidence))
        self._persist(state, current_state, evidence)
        yield from self._finish(
            state,
            current_state,
            evidence,
            last_summary,
            last_outcome,
        )

    @staticmethod
    def _load_requirement(state: AgentRuntimeState) -> ExecutionRequirement:
        try:
            requirement = execution_requirement_from_state(state.context.state)
            return requirement.require_ready("research")
        except ValueError as exc:
            raise ResearchPipelineError(str(exc)) from exc

    @staticmethod
    def _materialize(
        action: ResearchAction,
        research: ResearchRequirement,
        root: ExecutionRequirement,
        evidence_by_result: dict[str, EvidenceSnapshot],
    ) -> MaterializedResearchAction:
        try:
            return materialize_research_action(
                action=action,
                research_requirement=research,
                runtime=root.runtime,
                asset_snapshot=root.asset_snapshot,
                evidence_by_result=evidence_by_result,
            )
        except ResearchExecutionError as exc:
            raise ResearchPipelineError(exc.code) from exc

    def _execute_action(
        self,
        state: AgentRuntimeState,
        materialized: MaterializedResearchAction,
        *,
        plan_id: str,
    ) -> Generator[RenderEvent, None, PlanExecutionOutcome | None]:
        try:
            return (
                yield from self._plan_pipeline.execute_requirement(
                    state,
                    materialized.requirement,
                    plan_id=plan_id,
                )
            )
        except PlanPipelineError as exc:
            raise ResearchPipelineError(exc.code, str(exc)) from exc

    @staticmethod
    def _project_evidence(
        materialized: MaterializedResearchAction,
        outcome: PlanExecutionOutcome,
        *,
        evidence_index: int,
        research: ResearchRequirement,
    ) -> EvidenceSnapshot:
        execution = outcome.primary_execution
        result_id = str(
            execution.get("result_set_id") or outcome.plan.presentation.primary_result
        )
        try:
            return project_evidence_snapshot(
                materialized=materialized,
                plan_id=outcome.plan.id,
                task_id=outcome.plan.presentation.primary_result,
                result_id=result_id,
                rows=outcome.primary_rows,
                fields=[str(item) for item in execution.get("fields") or []],
                evidence_index=evidence_index,
                max_rows=research.budget.max_evidence_rows,
                max_chars=research.budget.max_evidence_chars,
            )
        except ResearchExecutionError as exc:
            raise ResearchPipelineError(exc.code) from exc

    def _persist(
        self,
        state: AgentRuntimeState,
        research_state: ResearchState,
        evidence: list[EvidenceSnapshot],
    ) -> None:
        state.context.state["research_state"] = research_state.model_dump(mode="json")
        state.context.state["research_evidence"] = [
            item.model_dump(mode="json") for item in evidence
        ]
        agent_run_repository.update_run(
            self._session,
            state.run,
            derived_state=state.persistable_context(),
        )
        self._session.commit()

    @staticmethod
    def _reserve_queries(
        state: ResearchState,
        count: int,
        started_at: float,
        requirement: ResearchRequirement,
    ) -> ResearchState:
        remaining = ResearchPipeline._remaining_duration(
            state.remaining_budget,
            started_at,
            requirement,
        )
        if count <= 0 or count > remaining.queries or remaining.duration_seconds <= 0:
            raise ResearchPipelineError(ResearchExecutionError.BUDGET_EXHAUSTED)
        return state.model_copy(
            update={
                "remaining_budget": remaining.model_copy(
                    update={"queries": remaining.queries - count}
                )
            }
        )

    @staticmethod
    def _remaining_duration(
        budget: ResearchRemainingBudget,
        started_at: float,
        requirement: ResearchRequirement,
    ) -> ResearchRemainingBudget:
        elapsed = int(time.monotonic() - started_at)
        return budget.model_copy(
            update={
                "duration_seconds": max(
                    0,
                    requirement.budget.max_duration_seconds - elapsed,
                )
            }
        )

    @staticmethod
    def _remaining_state(
        state: ResearchState,
        started_at: float,
        requirement: ResearchRequirement,
    ) -> ResearchState:
        return state.model_copy(
            update={
                "remaining_budget": ResearchPipeline._remaining_duration(
                    state.remaining_budget,
                    started_at,
                    requirement,
                )
            }
        )

    @staticmethod
    def _budget_exhausted(state: ResearchState) -> bool:
        budget = state.remaining_budget
        return (
            budget.iterations <= 0
            or budget.queries <= 0
            or budget.model_calls <= 0
            or budget.duration_seconds <= 0
        )

    @staticmethod
    def _terminal_state(
        state: ResearchState,
        *,
        status: ResearchRunStatus,
        reason: ResearchTerminationReason,
    ) -> ResearchState:
        return state.model_copy(update={"status": status, "finish_reason": reason})

    @staticmethod
    def _budget_terminal(state: ResearchState, has_evidence: bool) -> ResearchState:
        return ResearchPipeline._terminal_state(
            state,
            status=(
                ResearchRunStatus.PARTIAL
                if has_evidence
                else ResearchRunStatus.BUDGET_EXHAUSTED
            ),
            reason=ResearchTerminationReason.BUDGET_EXHAUSTED,
        )

    @staticmethod
    def _asset_catalog(
        root_requirement: ExecutionRequirement,
    ) -> tuple[dict[str, Any], ...]:
        raw = root_requirement.asset_snapshot.get("research_assets")
        if not isinstance(raw, dict):
            raise ResearchPipelineError("RESEARCH_ASSET_SNAPSHOT_REQUIRED")
        catalog: list[dict[str, Any]] = []
        for ref, item in sorted(raw.items()):
            if not isinstance(ref, str) or not isinstance(item, dict):
                raise ResearchPipelineError("RESEARCH_ASSET_SNAPSHOT_INVALID")
            catalog.append(
                {
                "ref": ref,
                "asset_type": item.get("asset_type"),
                "display_name": item.get("display_name"),
                "biz_name": item.get("biz_name"),
                "description": item.get("description"),
                }
            )
        return tuple(catalog)

    def _finish(
        self,
        state: AgentRuntimeState,
        research_state: ResearchState,
        evidence: list[EvidenceSnapshot],
        summary: str,
        outcome: PlanExecutionOutcome | None,
    ) -> Iterator[RenderEvent]:
        evidence_refs = "、".join(item.evidence_id for item in evidence) or "无"
        answer = (
            f"{summary}\n\n"
            f"Research 结束原因："
            f"{research_state.finish_reason.value if research_state.finish_reason else 'unknown'}。"
            f"证据引用：{evidence_refs}。"
        )
        execution = outcome.primary_execution if outcome is not None else None
        yield from self._lifecycle.finish(
            state,
            answer=answer,
            chart={},
            sql=execution.get("sql") if execution is not None else None,
            full_data=outcome.primary_rows if outcome is not None else None,
            execution=execution,
        )


def _finish_state(
    reason: ResearchFinishReason,
) -> tuple[ResearchRunStatus, ResearchTerminationReason]:
    mapping = {
        ResearchFinishReason.SUFFICIENT_EVIDENCE: (
            ResearchRunStatus.SUCCEEDED,
            ResearchTerminationReason.SUFFICIENT_EVIDENCE,
        ),
        ResearchFinishReason.PREMISE_NOT_SUPPORTED: (
            ResearchRunStatus.SUCCEEDED,
            ResearchTerminationReason.PREMISE_NOT_SUPPORTED,
        ),
        ResearchFinishReason.NO_NEW_DIRECTION: (
            ResearchRunStatus.SUCCEEDED,
            ResearchTerminationReason.NO_NEW_DIRECTION,
        ),
        ResearchFinishReason.DATA_INSUFFICIENT: (
            ResearchRunStatus.FAILED,
            ResearchTerminationReason.DATA_INSUFFICIENT,
        ),
        ResearchFinishReason.NEEDS_CLARIFICATION: (
            ResearchRunStatus.NEEDS_CLARIFICATION,
            ResearchTerminationReason.NEEDS_CLARIFICATION,
        ),
        ResearchFinishReason.BUDGET_EXHAUSTED: (
            ResearchRunStatus.PARTIAL,
            ResearchTerminationReason.BUDGET_EXHAUSTED,
        ),
    }
    return mapping[reason]


__all__ = [
    "ResearchPipeline",
    "ResearchPipelineDependencies",
    "ResearchPipelineError",
]
