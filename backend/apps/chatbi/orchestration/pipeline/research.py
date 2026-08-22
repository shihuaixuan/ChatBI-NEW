"""Research 第三阶段：受控模型决策与已证明子计划的动态闭环。"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Generator, Iterator, Sequence
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
    ResearchActionFailure,
    ResearchContributionAction,
    ResearchDrilldownAction,
    ResearchFilterFromResultAction,
    ResearchFinishReason,
    ResearchHypothesis,
    ResearchHypothesisStatus,
    ResearchIterationRecord,
    ResearchRemainingBudget,
    ResearchRequirement,
    ResearchRunStatus,
    ResearchState,
    ResearchTerminationReason,
    ResearchValidateHypothesisAction,
)
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.pipeline.plan_mode import (
    PlanExecutionOutcome,
    PlanPipeline,
    PlanPipelineError,
)
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.research.action_batches import build_research_action_batches
from apps.chatbi.services.research.actions import (
    MaterializedResearchAction,
    initial_compare_action,
    materialize_research_action,
    merge_research_action_requirements,
    research_action_fingerprint,
)
from apps.chatbi.services.research.evidence import (
    premise_supported,
    project_evidence_snapshot,
)
from apps.chatbi.services.research.hypotheses import (
    apply_deterministic_hypothesis_result,
    apply_hypothesis_updates,
    evaluate_hypothesis_status,
)
from apps.chatbi.services.research.policy import ResearchPolicy
from apps.chatbi.services.research.report import compose_research_report
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
            hypotheses=current_state.hypotheses,
        )
        current_state = self._reserve_queries(
            current_state,
            len(materialized.requirement.query_requirements),
            started_at,
            research,
        )
        outcome = yield from self._execute_action(
            state,
            materialized.requirement,
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
                "premise_supported": premise_supported(
                    research.goal, initial_evidence
                ),
                "remaining_budget": self._remaining_duration(
                    current_state.remaining_budget,
                    started_at,
                    research,
                ),
            }
        )
        self._persist(state, current_state, evidence)
        premise = current_state.premise_supported
        if premise is False:
            current_state = self._terminal_state(
                current_state,
                status=ResearchRunStatus.SUCCEEDED,
                reason=ResearchTerminationReason.PREMISE_NOT_SUPPORTED,
            )
            last_summary = "实际数据不支持问题中声明的变化方向，研究已在现象确认后结束。"
            current_state = self._attach_report(current_state, evidence, last_summary)
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
                current_state = self._budget_terminal(
                    current_state,
                    has_evidence=len(evidence) > 1,
                    completion_met=_declared_completion_requirements_met(
                        research,
                        current_state,
                        evidence,
                    ),
                )
                last_summary = (
                    "Research 已完成用户要求，并达到服务端预算上限。"
                    if current_state.status is ResearchRunStatus.SUCCEEDED
                    else "Research 已达到服务端预算上限。"
                )
                break
            # 迭代预算在调用前预留；模型调用预算按真实调用次数扣减，
            # 结构修复不能隐藏第二次调用。
            current_state = current_state.model_copy(
                update={
                    "iteration": current_state.iteration + 1,
                    "remaining_budget": current_state.remaining_budget.model_copy(
                        update={
                            "iterations": current_state.remaining_budget.iterations - 1,
                        }
                    ),
                }
            )
            iteration_failures: list[ResearchActionFailure] = []
            self._persist(state, current_state, evidence)
            try:
                if hasattr(self._policy, "decide_with_usage"):
                    policy_result = self._policy.decide_with_usage(
                        requirement=research,
                        state=current_state,
                        evidence=tuple(evidence),
                        asset_catalog=self._asset_catalog(root_requirement),
                        max_model_calls=(
                            current_state.remaining_budget.model_calls
                        ),
                    )
                    decision = policy_result.decision
                    model_calls = policy_result.model_calls
                else:
                    decision = self._policy.decide(
                        requirement=research,
                        state=current_state,
                        evidence=tuple(evidence),
                        asset_catalog=self._asset_catalog(root_requirement),
                    )
                    model_calls = 1
            except ResearchExecutionError as exc:
                consumed_calls = exc.details.get("model_calls")
                if isinstance(consumed_calls, int) and consumed_calls > 0:
                    current_state = current_state.model_copy(
                        update={
                            "remaining_budget": (
                                current_state.remaining_budget.model_copy(
                                    update={
                                        "model_calls": max(
                                            0,
                                            current_state.remaining_budget.model_calls
                                            - consumed_calls,
                                        )
                                    }
                                )
                            )
                        }
                    )
                    self._persist(state, current_state, evidence)
                raise ResearchPipelineError(exc.code) from exc
            current_state = current_state.model_copy(
                update={
                    "remaining_budget": current_state.remaining_budget.model_copy(
                        update={
                            "model_calls": max(
                                0,
                                current_state.remaining_budget.model_calls
                                - model_calls,
                            )
                        }
                    )
                }
            )
            last_summary = decision.assessment.evidence_summary
            decision_fingerprint = _research_decision_fingerprint(decision)
            try:
                updated_hypotheses = apply_hypothesis_updates(
                    current_state.hypotheses,
                    decision,
                    tuple(evidence),
                )
            except ResearchExecutionError as exc:
                # 非法假设变化丢弃本轮假设更新并记录归属，不终止研究（doc §9.13）。
                updated_hypotheses = current_state.hypotheses
                iteration_failures.append(
                    ResearchActionFailure(
                        action_fingerprint=(
                            f"hypothesis-decision:{decision_fingerprint[:16]}"
                        ),
                        error_code=exc.code,
                    )
                )
            current_state = current_state.model_copy(
                update={
                    "hypotheses": updated_hypotheses,
                    "assessment_summaries": (
                        *current_state.assessment_summaries,
                        decision.assessment.evidence_summary,
                    ),
                }
            )
            if decision.decision.type == "finish":
                iteration_record = ResearchIterationRecord(
                    iteration=current_state.iteration,
                    policy_decision_fingerprint=decision_fingerprint,
                )
                if not _finish_condition_is_valid(
                    decision.decision.reason,
                    research,
                    current_state,
                    evidence,
                ):
                    current_state = current_state.model_copy(
                        update={
                            "iteration_records": (
                                *current_state.iteration_records,
                                iteration_record.model_copy(
                                    update={
                                        "failed_actions": (
                                            ResearchActionFailure(
                                                action_fingerprint="finish",
                                                error_code=(
                                                    ResearchExecutionError
                                                    .FINISH_CONDITION_INVALID
                                                ),
                                            ),
                                        )
                                    }
                                ),
                            )
                        }
                    )
                    last_summary = "当前证据不足以支持该结束原因，继续收集证据。"
                    self._persist(state, current_state, evidence)
                    continue
                current_state = current_state.model_copy(
                    update={
                        "iteration_records": (
                            *current_state.iteration_records,
                            iteration_record,
                        )
                    }
                )
                status, reason = _finish_state(decision.decision.reason)
                current_state = self._terminal_state(
                    current_state,
                    status=status,
                    reason=reason,
                )
                break

            try:
                action_batches = build_research_action_batches(
                    decision.decision.actions,
                    existing_result_ids=set(evidence_by_result),
                    existing_evidence_ids={item.evidence_id for item in evidence},
                    max_actions_per_batch=research.budget.max_actions_per_iteration,
                )
            except ResearchExecutionError as exc:
                raise ResearchPipelineError(exc.code) from exc
            pending_fingerprints = set(current_state.executed_action_fingerprints)
            new_evidence_count = 0
            iteration_plan_ids: list[str] = []
            iteration_result_ids: list[str] = []
            iteration_evidence_ids: list[str] = []
            iteration_action_fingerprints: list[str] = []
            iteration_dimension_refs: set[str] = set()
            iteration_driver_metric_refs: set[str] = set()
            iteration_hierarchy_ids: set[str] = set()
            iteration_contribution_dimension_refs: set[str] = set()
            for batch_index, action_batch in enumerate(action_batches, start=1):
                materialized_actions = []
                selected_fingerprints = set(pending_fingerprints)
                for action in action_batch:
                    action_fingerprint = research_action_fingerprint(action)
                    if action_fingerprint in selected_fingerprints:
                        iteration_failures.append(
                            ResearchActionFailure(
                                action_fingerprint=action_fingerprint,
                                error_code=ResearchExecutionError.ACTION_DUPLICATED,
                            )
                        )
                        continue
                    selected_fingerprints.add(action_fingerprint)
                    try:
                        materialized_actions.append(
                            self._materialize(
                                action,
                                research,
                                root_requirement,
                                evidence_by_result,
                                hypotheses=current_state.hypotheses,
                            )
                        )
                    except (ResearchExecutionError, ResearchPipelineError) as exc:
                        # 单个动作失败不终止研究（doc §9.13）：记录归属后跳过，
                        # 同批其余独立动作继续执行。_materialize 会把
                        # ResearchExecutionError 转为 ResearchPipelineError。
                        iteration_failures.append(
                            ResearchActionFailure(
                                action_fingerprint=action_fingerprint,
                                error_code=getattr(exc, "code", str(exc)),
                            )
                        )
                if not materialized_actions:
                    break
                materialized_actions = tuple(materialized_actions)
                required_queries = sum(
                    len(item.requirement.query_requirements)
                    for item in materialized_actions
                )
                affordable = self._remaining_duration(
                    current_state.remaining_budget,
                    started_at,
                    research,
                )
                if required_queries > affordable.queries:
                    materialized_actions = _affordable_completion_actions(
                        materialized_actions,
                        research,
                        current_state,
                        affordable.queries,
                    )
                    required_queries = sum(
                        len(item.requirement.query_requirements)
                        for item in materialized_actions
                    )
                if (
                    not materialized_actions
                    or required_queries > affordable.queries
                    or affordable.duration_seconds <= 0
                ):
                    # 同轮独立动作必须整批预留预算；预算不足时整批不执行，
                    # 也不能把尚未执行的动作写入已执行指纹。
                    current_state = self._budget_terminal(
                        current_state.model_copy(
                            update={"remaining_budget": affordable}
                        ),
                        has_evidence=len(evidence) > 1,
                        completion_met=_declared_completion_requirements_met(
                            research,
                            current_state,
                            evidence,
                        ),
                    )
                    last_summary = (
                        "Research 已完成用户要求，并达到服务端预算上限。"
                        if current_state.status is ResearchRunStatus.SUCCEEDED
                        else "Research 已达到服务端预算上限。"
                    )
                    break
                current_state = self._reserve_queries(
                    current_state,
                    required_queries,
                    started_at,
                    research,
                )
                for (
                    sub_batch_index,
                    sub_batch,
                ) in enumerate(
                    self._split_by_plan_capacity(
                        materialized_actions,
                        max_query_tasks=self._plan_pipeline.max_query_tasks,
                    ),
                    start=1,
                ):
                    batch_requirement = merge_research_action_requirements(sub_batch)
                    plan_id = (
                        f"plan:{research_id}:{current_state.iteration}"
                        f":{batch_index}-{sub_batch_index}"
                    )
                    iteration_plan_ids.append(plan_id)
                    try:
                        outcome = yield from self._execute_action(
                            state,
                            batch_requirement,
                            plan_id=plan_id,
                        )
                    except ResearchPipelineError as exc:
                        # 单个动作自身超过计划容量时可以归属到该动作；严格语义
                        # 证明、Schema/Contract 和执行基础设施错误必须终止整个 Run。
                        if not (
                            exc.code == "PLAN_QUERY_GROUP_LIMIT_EXCEEDED"
                            and len(sub_batch) == 1
                        ):
                            raise
                        for failed_item in sub_batch:
                            iteration_failures.append(
                                ResearchActionFailure(
                                    action_fingerprint=failed_item.fingerprint,
                                    error_code=exc.code,
                                )
                            )
                        continue
                    if outcome is None:
                        return
                    last_outcome = outcome
                    for item in sub_batch:
                        iteration_action_fingerprints.append(item.fingerprint)
                        action_result = self._action_result(
                            outcome,
                            item,
                            action_count=len(sub_batch),
                        )
                        if action_result is None:
                            iteration_failures.append(
                                ResearchActionFailure(
                                    action_fingerprint=item.fingerprint,
                                    error_code=self._action_failure_code(
                                        state,
                                        item.primary_requirement_id,
                                    ),
                                )
                            )
                            continue
                        execution, rows = action_result
                        snapshot = self._project_evidence(
                            item,
                            outcome,
                            evidence_index=len(evidence) + 1,
                            research=research,
                            primary_execution=execution,
                            primary_rows=rows,
                            batch_id=plan_id,
                        )
                        evidence.append(snapshot)
                        pending_fingerprints.add(item.fingerprint)
                        evidence_by_result[snapshot.result_id] = snapshot
                        iteration_result_ids.append(snapshot.result_id)
                        iteration_evidence_ids.append(snapshot.evidence_id)
                        iteration_dimension_refs.update(_action_dimension_refs(item.action))
                        iteration_hierarchy_ids.update(
                            _completed_hierarchy_ids(item.action, research)
                        )
                        if isinstance(item.action, ResearchContributionAction):
                            iteration_contribution_dimension_refs.add(
                                item.action.dimension_ref
                            )
                        if snapshot.statistics.row_count > 0:
                            new_evidence_count += 1
                        if isinstance(item.action, ResearchValidateHypothesisAction):
                            hypothesis_status = evaluate_hypothesis_status(
                                metric_refs=item.action.metric_refs,
                                dimension_refs=item.action.dimension_refs,
                                requirement=research,
                                materialized=item,
                                rows=rows,
                                evidence=snapshot,
                            )
                            if hypothesis_status is not ResearchHypothesisStatus.INVALID:
                                iteration_driver_metric_refs.update(
                                    metric_ref
                                    for metric_ref in item.action.metric_refs
                                    if metric_ref
                                    in research.scope.driver_metric_refs
                                )
                            try:
                                current_state = current_state.model_copy(
                                    update={
                                        "hypotheses": (
                                            apply_deterministic_hypothesis_result(
                                                hypotheses=current_state.hypotheses,
                                                hypothesis_id=item.action.hypothesis_id,
                                                status=hypothesis_status,
                                                evidence_id=snapshot.evidence_id,
                                            )
                                        )
                                    }
                                )
                            except ResearchExecutionError as exc:
                                # 假设不存在时保留证据、记录归属，不终止研究（doc §9.13）。
                                iteration_failures.append(
                                    ResearchActionFailure(
                                        action_fingerprint=item.fingerprint,
                                        error_code=exc.code,
                                    )
                                )
                if current_state.finish_reason is not None:
                    break
            current_state = current_state.model_copy(
                update={
                    "evidence_ids": tuple(item.evidence_id for item in evidence),
                    "executed_action_fingerprints": tuple(sorted(pending_fingerprints)),
                    "iteration_records": (
                        *current_state.iteration_records,
                        ResearchIterationRecord(
                            iteration=current_state.iteration,
                            policy_decision_fingerprint=decision_fingerprint,
                            action_fingerprints=tuple(iteration_action_fingerprints),
                            plan_ids=tuple(iteration_plan_ids),
                            result_ids=tuple(iteration_result_ids),
                            evidence_ids=tuple(iteration_evidence_ids),
                            failed_actions=tuple(iteration_failures),
                        ),
                    ),
                    "covered_dimension_refs": tuple(
                        sorted(
                            {
                                *current_state.covered_dimension_refs,
                                *iteration_dimension_refs,
                            }
                        )
                    ),
                    "covered_driver_metric_refs": tuple(
                        sorted(
                            {
                                *current_state.covered_driver_metric_refs,
                                *iteration_driver_metric_refs,
                            }
                        )
                    ),
                    "covered_hierarchy_ids": tuple(
                        sorted(
                            {
                                *current_state.covered_hierarchy_ids,
                                *iteration_hierarchy_ids,
                            }
                        )
                    ),
                    "covered_contribution_dimension_refs": tuple(
                        sorted(
                            {
                                *current_state.covered_contribution_dimension_refs,
                                *iteration_contribution_dimension_refs,
                            }
                        )
                    ),
                    "consecutive_no_new_direction": (
                        current_state.consecutive_no_new_direction + 1
                        if new_evidence_count == 0
                        else 0
                    ),
                    "remaining_budget": self._remaining_duration(
                        current_state.remaining_budget,
                        started_at,
                        research,
                    ),
                }
            )
            self._persist(state, current_state, evidence)
            if current_state.finish_reason is not None:
                break
            if new_evidence_count == 0:
                if current_state.consecutive_no_new_direction < 2:
                    # 给策略一轮恢复机会：读取失败动作归属后改选方向或显式收口。
                    continue
                completed = _completion_requirements_met(
                    research,
                    current_state,
                    evidence,
                )
                current_state = self._terminal_state(
                    current_state,
                    status=(
                        ResearchRunStatus.SUCCEEDED
                        if completed
                        else (
                            ResearchRunStatus.PARTIAL
                            if len(evidence) > 1
                            else ResearchRunStatus.FAILED
                        )
                    ),
                    reason=(
                        ResearchTerminationReason.NO_NEW_DIRECTION
                        if completed
                        else (
                            ResearchTerminationReason.PARTIAL_FAILURE
                            if len(evidence) > 1
                            else ResearchTerminationReason.DATA_INSUFFICIENT
                        )
                    ),
                )
                last_summary = "连续两轮没有获得新的研究方向，研究提前收口。"
                break
        else:
            current_state = self._budget_terminal(
                current_state,
                has_evidence=len(evidence) > 1,
                completion_met=_declared_completion_requirements_met(
                    research,
                    current_state,
                    evidence,
                ),
            )
            last_summary = (
                "Research 已完成用户要求，并达到最大迭代次数。"
                if current_state.status is ResearchRunStatus.SUCCEEDED
                else "Research 已达到最大迭代次数。"
            )

        if current_state.finish_reason is None:
            current_state = self._budget_terminal(
                current_state,
                has_evidence=len(evidence) > 1,
                completion_met=_declared_completion_requirements_met(
                    research,
                    current_state,
                    evidence,
                ),
            )
        current_state = self._attach_report(current_state, evidence, last_summary)
        self._persist(state, current_state, evidence)
        yield from self._finish(
            state,
            current_state,
            evidence,
            last_summary,
            last_outcome,
        )

    @staticmethod
    def _split_by_plan_capacity(
        materialized_actions: Sequence[MaterializedResearchAction],
        *,
        max_query_tasks: int,
    ) -> list[list[MaterializedResearchAction]]:
        """按 Plan 管道的查询组容量拆分动作批次。

        Plan 校验器限制单个需求最多 max_query_tasks 个查询组，合并批次超限
        会被整体拒绝（PLAN_QUERY_GROUP_LIMIT_EXCEEDED）。这里把合并后的动作
        拆成若干不超过容量的子批次顺序执行；单个动作自身超限时保持原样，
        交由 Plan 阶段报错并按动作失败处理（doc §9.13）。
        """
        if max_query_tasks <= 0:
            return [list(materialized_actions)]
        sub_batches: list[list[MaterializedResearchAction]] = []
        current: list[MaterializedResearchAction] = []
        current_size = 0
        for item in materialized_actions:
            size = len(item.requirement.query_requirements)
            if current and current_size + size > max_query_tasks:
                sub_batches.append(current)
                current = []
                current_size = 0
            current.append(item)
            current_size += size
        if current:
            sub_batches.append(current)
        return sub_batches

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
        hypotheses: tuple[ResearchHypothesis, ...] = (),
    ) -> MaterializedResearchAction:
        try:
            return materialize_research_action(
                action=action,
                research_requirement=research,
                runtime=root.runtime,
                asset_snapshot=root.asset_snapshot,
                evidence_by_result=evidence_by_result,
                hypotheses=hypotheses,
            )
        except ResearchExecutionError as exc:
            raise ResearchPipelineError(exc.code) from exc

    def _execute_action(
        self,
        state: AgentRuntimeState,
        requirement: ExecutionRequirement,
        *,
        plan_id: str,
    ) -> Generator[RenderEvent, None, PlanExecutionOutcome | None]:
        try:
            return (
                yield from self._plan_pipeline.execute_requirement(
                    state,
                    requirement,
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
        primary_execution: dict[str, Any] | None = None,
        primary_rows: list[dict[str, Any]] | None = None,
        batch_id: str | None = None,
    ) -> EvidenceSnapshot:
        execution = primary_execution or outcome.primary_execution
        rows = outcome.primary_rows if primary_rows is None else primary_rows
        result_id = str(
            execution.get("result_set_id") or outcome.plan.presentation.primary_result
        )
        try:
            return project_evidence_snapshot(
                materialized=materialized,
                plan_id=outcome.plan.id,
                task_id=materialized.primary_requirement_id,
                result_id=result_id,
                rows=rows,
                fields=[str(item) for item in execution.get("fields") or []],
                evidence_index=evidence_index,
                max_rows=research.budget.max_evidence_rows,
                max_chars=research.budget.max_evidence_chars,
                hypothesis_ids=materialized.hypothesis_ids,
                batch_id=batch_id,
            )
        except ResearchExecutionError as exc:
            raise ResearchPipelineError(exc.code) from exc

    @staticmethod
    def _action_result(
        outcome: PlanExecutionOutcome,
        materialized: MaterializedResearchAction,
        *,
        action_count: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        # Planner 给 query/post_calculation 任务 id 加 q:/c: 前缀，execution_records
        # 以任务 id 为键；动作主需求 id 可能是查询或计算 id，需要映射回任务 id。
        requirement_id = materialized.primary_requirement_id
        for task_id in (f"c:{requirement_id}", f"q:{requirement_id}", requirement_id):
            execution = outcome.execution_records.get(task_id)
            if execution is not None:
                return execution, outcome.full_data_records.get(task_id) or []
        if action_count == 1:
            return outcome.primary_execution, outcome.primary_rows
        return None

    @staticmethod
    def _action_failure_code(
        state: AgentRuntimeState,
        requirement_id: str,
    ) -> str:
        # 与 _action_result 相同：需求 id 需要映射回带 q:/c: 前缀的任务 id。
        task_states = state.context.state.get("plan_task_states")
        if isinstance(task_states, dict):
            for task_id in (
                f"c:{requirement_id}",
                f"q:{requirement_id}",
                requirement_id,
            ):
                task_state = task_states.get(task_id)
                if (
                    isinstance(task_state, dict)
                    and task_state.get("error_code")
                ):
                    return str(task_state["error_code"])
        return "RESEARCH_ACTION_RESULT_MISSING"

    @staticmethod
    def _attach_report(
        state: ResearchState,
        evidence: list[EvidenceSnapshot],
        summary: str,
    ) -> ResearchState:
        try:
            report = compose_research_report(
                state=state,
                evidence=tuple(evidence),
                summary=summary,
            )
        except ResearchExecutionError as exc:
            raise ResearchPipelineError(exc.code) from exc
        return state.model_copy(update={"report": report})

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
    def _budget_terminal(
        state: ResearchState,
        *,
        has_evidence: bool,
        completion_met: bool = False,
    ) -> ResearchState:
        # 预算只限制继续采集证据；已经满足用户完成目标时不能降级为部分成功。
        if completion_met:
            return ResearchPipeline._terminal_state(
                state,
                status=ResearchRunStatus.SUCCEEDED,
                reason=ResearchTerminationReason.SUFFICIENT_EVIDENCE,
            )
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
        report = research_state.report
        answer = json.dumps(
            report.model_dump(mode="json")
            if report is not None
            else {
                "summary": summary,
                "evidence_ids": [item.evidence_id for item in evidence],
            },
            ensure_ascii=False,
            sort_keys=True,
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


def _finish_condition_is_valid(
    reason: ResearchFinishReason,
    requirement: ResearchRequirement,
    state: ResearchState,
    evidence: list[EvidenceSnapshot],
) -> bool:
    """服务端确认模型声明的前提结束原因与当前证据一致。"""

    if reason is ResearchFinishReason.PREMISE_NOT_SUPPORTED:
        return state.premise_supported is False
    if reason in {
        ResearchFinishReason.SUFFICIENT_EVIDENCE,
        ResearchFinishReason.NO_NEW_DIRECTION,
    }:
        return _completion_requirements_met(requirement, state, evidence)
    return True


def _completion_requirements_met(
    requirement: ResearchRequirement,
    state: ResearchState,
    evidence: list[EvidenceSnapshot],
) -> bool:
    """统一判断用户目标是否已由真实研究动作和当前 Run 证据覆盖。"""

    if state.premise_supported is not True or len(evidence) <= 1:
        return False
    if not set(requirement.required_dimension_refs) <= set(
        state.covered_dimension_refs
    ):
        return False
    if not set(requirement.required_driver_metric_refs) <= set(
        state.covered_driver_metric_refs
    ):
        return False
    if not set(requirement.required_hierarchy_ids) <= set(
        state.covered_hierarchy_ids
    ):
        return False
    if not set(requirement.required_contribution_dimension_refs) <= set(
        state.covered_contribution_dimension_refs
    ):
        return False
    terminal_hypotheses = {
        item.id: item
        for item in state.hypotheses
        if item.status
        in {
            ResearchHypothesisStatus.SUPPORTED,
            ResearchHypothesisStatus.WEAKENED,
            ResearchHypothesisStatus.INCONCLUSIVE,
        }
        and item.evidence_ids
    }
    for driver_ref in requirement.required_driver_metric_refs:
        if not any(
            driver_ref in item.metric_refs
            and any(
                hypothesis_id in terminal_hypotheses
                and item.evidence_id
                in terminal_hypotheses[hypothesis_id].evidence_ids
                for hypothesis_id in item.hypothesis_ids
            )
            for item in evidence
        ):
            return False
    return True


def _completed_hierarchy_ids(
    action: Any,
    requirement: ResearchRequirement,
) -> set[str]:
    """识别显式下钻及与治理相邻层级等价的结果驱动分解。"""

    if isinstance(action, ResearchDrilldownAction):
        return {action.hierarchy_id}
    if not isinstance(action, ResearchFilterFromResultAction):
        return set()
    if action.analysis.type != "breakdown" or action.analysis.dimension_ref is None:
        return set()
    current_ref = action.target_dimension_ref
    next_ref = action.analysis.dimension_ref
    completed: set[str] = set()
    for hierarchy in requirement.scope.hierarchies:
        for index in range(len(hierarchy.dimension_refs) - 1):
            if hierarchy.dimension_refs[index : index + 2] == (
                current_ref,
                next_ref,
            ):
                completed.add(hierarchy.id)
    return completed


def _affordable_completion_actions(
    actions: tuple[MaterializedResearchAction, ...],
    requirement: ResearchRequirement,
    state: ResearchState,
    query_budget: int,
) -> tuple[MaterializedResearchAction, ...]:
    """批次超预算时只保留能够推进尚未完成显式目标的动作。"""

    if query_budget <= 0:
        return ()
    outstanding_dimensions = set(requirement.required_dimension_refs) - set(
        state.covered_dimension_refs
    )
    outstanding_drivers = set(requirement.required_driver_metric_refs) - set(
        state.covered_driver_metric_refs
    )
    outstanding_hierarchies = set(requirement.required_hierarchy_ids) - set(
        state.covered_hierarchy_ids
    )
    outstanding_contributions = set(
        requirement.required_contribution_dimension_refs
    ) - set(state.covered_contribution_dimension_refs)
    if not any(
        (
            outstanding_dimensions,
            outstanding_drivers,
            outstanding_hierarchies,
            outstanding_contributions,
        )
    ):
        return ()

    ranked: list[tuple[int, int, MaterializedResearchAction]] = []
    for index, item in enumerate(actions):
        action = item.action
        priority = None
        if (
            isinstance(action, ResearchContributionAction)
            and action.dimension_ref in outstanding_contributions
        ):
            priority = 0
        elif _completed_hierarchy_ids(action, requirement) & outstanding_hierarchies:
            priority = 1
        elif (
            isinstance(action, ResearchValidateHypothesisAction)
            and set(action.metric_refs) & outstanding_drivers
        ):
            priority = 2
        elif set(_action_dimension_refs(action)) & outstanding_dimensions:
            priority = 3
        if priority is not None:
            ranked.append((priority, index, item))

    selected: list[MaterializedResearchAction] = []
    remaining = query_budget
    for _priority, _index, item in sorted(ranked):
        cost = len(item.requirement.query_requirements)
        if cost > remaining:
            if not selected:
                return ()
            continue
        selected.append(item)
        remaining -= cost
    return tuple(selected)


def _declared_completion_requirements_met(
    requirement: ResearchRequirement,
    state: ResearchState,
    evidence: list[EvidenceSnapshot],
) -> bool:
    """预算收口时只自动确认服务端能够明确判断的显式用户目标。"""

    has_declared_requirements = any(
        (
            requirement.required_dimension_refs,
            requirement.required_driver_metric_refs,
            requirement.required_hierarchy_ids,
            requirement.required_contribution_dimension_refs,
        )
    )
    return has_declared_requirements and _completion_requirements_met(
        requirement,
        state,
        evidence,
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


def _research_decision_fingerprint(decision: Any) -> str:
    """记录每轮结构化决策指纹，便于审计和回放。"""

    payload = json.dumps(
        decision.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _action_dimension_refs(action: ResearchAction) -> tuple[str, ...]:
    """从动作 DTO 投影已覆盖维度，不读取物理字段。"""

    refs: list[str] = []
    for name in ("dimension_ref", "current_dimension_ref", "next_dimension_ref"):
        value = getattr(action, name, None)
        if isinstance(value, str):
            refs.append(value)
    value = getattr(action, "dimension_refs", ())
    if isinstance(value, (tuple, list)):
        refs.extend(item for item in value if isinstance(item, str))
    target = getattr(action, "target_dimension_ref", None)
    if isinstance(target, str):
        refs.append(target)
    analysis = getattr(action, "analysis", None)
    analysis_dimension = getattr(analysis, "dimension_ref", None)
    if isinstance(analysis_dimension, str):
        refs.append(analysis_dimension)
    return tuple(dict.fromkeys(refs))


def _action_metric_refs(action: ResearchAction) -> tuple[str, ...]:
    """从动作 DTO 投影已覆盖指标。"""

    refs: list[str] = []
    value = getattr(action, "metric_refs", ())
    refs.extend(item for item in value if isinstance(item, str))
    metric_ref = getattr(action, "metric_ref", None)
    if isinstance(metric_ref, str):
        refs.append(metric_ref)
    analysis = getattr(action, "analysis", None)
    analysis_metrics = getattr(analysis, "metric_refs", ())
    refs.extend(item for item in analysis_metrics if isinstance(item, str))
    return tuple(dict.fromkeys(refs))


__all__ = [
    "ResearchPipeline",
    "ResearchPipelineDependencies",
    "ResearchPipelineError",
]
