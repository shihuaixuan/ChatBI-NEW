"""Research Agent Harness：Function Calling 动态研究循环（doc38 §9）。

取代旧 ``ResearchPolicyDecision`` / ``ResearchAction`` 计划式循环：模型每轮
根据 Requirement、Working State 和 Observation 自己选择四个研究工具，服务端
只负责协议规则、预算、停止条件和持久化边界。没有全局 DAG，也没有 Action
物化器；每一步事实都落在阶段 4 的 ``ResearchToolCallCommit`` 边界上，
可追踪、可恢复、可取消。

主流程集中在 :meth:`ResearchAgentHarness.run` 一个方法内（§9.3.2）；
Tool 执行、批次校验和终态收口等复杂职责下沉为私有方法。阶段 7.5 起
主路径经由 :class:`~apps.chatbi.orchestration.pipeline.research_agent_pipeline.
ResearchAgentPipeline` 适配器接入 RunOrchestrator 分发（配置
``research_execution_mode="agent"`` 时生效）；此前新路径只在测试或
Shadow 场景运行。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from typing import Any

import orjson
from sqlmodel import Session

from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchBudgetUsage,
    ResearchCompletion,
    ResearchCompletionReason,
    ResearchRunSnapshot,
    ToolObservation,
    ToolObservationStatus,
)
from apps.chatbi.orchestration.agent.cancellation import AgentCancellationRequested
from apps.chatbi.orchestration.agent.messages import AgentMessage
from apps.chatbi.orchestration.agent.reasoning import AgentReasoner
from apps.chatbi.orchestration.agent.reasoning_profile import RESEARCH_PROFILE
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.orchestration.agent.tools.research import (
    RESEARCH_TOOL_NAMES,
    build_research_tool_registry,
)
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.evidence import (
    ANALYSIS_EVIDENCE_REGISTRY_KEY,
    EvidenceRegistry,
)
from apps.chatbi.services.research.agent_context import (
    build_premise_query_args,
    build_research_system_context,
    evaluate_premise_verdict,
    project_research_working_state,
)
from apps.chatbi.services.research.completion import evaluate_completion
from apps.chatbi.services.research.initial_plan import ResearchInitialPlanner
from apps.chatbi.services.research.report_draft import (
    build_final_report,
    build_partial_report,
)
from apps.chatbi.services.research.run_lifecycle import (
    RESEARCH_STATE_KEY,
    ResearchRecoveryReport,
    ResearchToolCallCommit,
    cancel_research_run,
    recover_research_run,
)
from apps.chatbi.services.research.state_snapshot import build_research_run_snapshot
from apps.chatbi.services.research.tool_context import ResearchToolContext
from apps.tool import (
    BudgetGuard,
    NeverCancelled,
    ToolCall,
    ToolErrorCategory,
    ToolResult,
    ToolStatus,
)
from apps.tool.concurrency import ToolBatchExecutionError, execute_tool_batch
from apps.trace import AgentTraceRecorder, DisabledAgentTraceRecorder

logger = logging.getLogger(__name__)

_RESULT_SETS_KEY = "result_sets"
_MAX_COMPLETION_EVIDENCE_IDS = 20


def _report_json(payload: dict[str, Any]) -> str:
    """报告草案统一以 JSON 字符串进入快照的 report_draft/final_report。"""

    return orjson.dumps(payload).decode("utf-8")


@dataclass(frozen=True)
class ResearchAgentRunOutcome:
    """一次 Harness 执行的终态结果。"""

    completion: ResearchCompletion
    stop_reason: str
    turns: int
    snapshot: ResearchRunSnapshot | None = None
    recovery: ResearchRecoveryReport | None = None


class ResearchAgentHarness:
    """服务端驱动的动态研究循环宿主。

    职责边界：模型只做工具选择；预算、重复、停止、取消和恢复全部由
    本类与服务端工具层强制（§9.2.5）。
    """

    def __init__(
        self,
        *,
        session: Any,
        config: AgentConfig,
        run_row: Any,
        model_client: Any,
        record: Any = None,
        semantic_runtime: Any = None,
        compute_engine: Any = None,
        result_store: Any = None,
        recorder: AgentTraceRecorder | None = None,
        cancellation: Any = None,
        observation_recorder: Any = None,
        context_state_overlay: dict[str, Any] | None = None,
        initial_planner: ResearchInitialPlanner | None = None,
    ) -> None:
        self._session = session
        self._config = config
        self._run_row = run_row
        self._record = record
        self._registry = build_research_tool_registry()
        self._reasoner = AgentReasoner(
            config,
            model_client,
            self._registry,
            recorder or DisabledAgentTraceRecorder(),
        )
        self._semantic_runtime = semantic_runtime
        self._compute_engine = compute_engine
        self._result_store = result_store
        self._cancellation = cancellation
        self._observation_recorder = observation_recorder
        # 主路径接线（阶段 7.5）：工具上下文需要路由期产出的边界输入
        # （盖戳后的 semantic_scope、permission_version），由适配层显式注入。
        self._context_state_overlay = dict(context_state_overlay or {})
        # 主路径显式注入 Planner；旧的直接 Harness 调用不注入时保持原循环。
        self._initial_planner = initial_planner

    # ------------------------------------------------------------------ #
    # 入口
    # ------------------------------------------------------------------ #

    def run(
        self,
        *,
        requirement: ResearchAgentRequirement | None = None,
        ctx: ResearchToolContext | None = None,
    ) -> ResearchAgentRunOutcome:
        """执行一次完整的动态研究循环。

        ``ctx`` 为空表示全新 Run（此时 ``requirement`` 必填）；传入恢复流程
        重建的 ``ctx`` 则从已持久化的事实继续。
        """

        if ctx is None:
            if requirement is None:
                raise TypeError("RESEARCH_HARNESS_REQUIREMENT_REQUIRED")
            if requirement.initial_plan is None and self._initial_planner is not None:
                requirement = ResearchAgentRequirement.model_validate(
                    {
                        **requirement.model_dump(mode="json"),
                        "initial_plan": self._initial_planner.plan(
                            requirement
                        ).model_dump(mode="json"),
                    }
                )
            ctx = self._initial_context(requirement)
            self._persist_initial_state(ctx)
        state = self._build_runtime_state(ctx)

        started_at = time.monotonic()
        stall_turns = 0
        structural_coverage_reminded = False
        turns = 0

        # 冻结 Requirement 带有首轮计划时，先完成所有当前可确定节点，
        if ctx.requirement.initial_plan is not None:
            outcome = self._execute_initial_plan(ctx, turns, started_at)
            if outcome is not None:
                return outcome
            if not ctx.initial_plan_complete():
                state.messages.append(
                    AgentMessage.user(
                        "<system-reminder>首轮基础计划已全部尝试，但存在失败或未满足的"
                        "最低结构覆盖缺口。只有根据当前 Evidence 和失败观察能够确定"
                        "新增方向时，才生成下一批工具调用。</system-reminder>"
                    )
                )

        while True:
            # 1. 取消信号检查
            if self._is_cancelled():
                return self._finalize_cancelled(ctx, turns, stage="loop_top")

            # 2. 预算检查
            exhaustion = self._budget_exhaustion(ctx, started_at)
            if exhaustion is not None:
                return self._finalize_server_stop(
                    ctx,
                    turns,
                    stop_reason="budget_exhausted",
                    reason_key="budget_exhausted",
                )

            # ---- 条件前提确认（§9.3.3）：只在有 premise 且未确认时执行一次 ----
            if (
                ctx.requirement.initial_plan is None
                and ctx.requirement.premise_to_verify is not None
                and ctx.premise_result is None
            ):
                outcome = self._run_premise_preflight(ctx, turns)
                if outcome is not None:
                    return outcome

            # ---- 最低结构覆盖只提供评估提示，不能替代内容充分性判断或启动强制收口 ----
            if (
                not structural_coverage_reminded
                and self._minimum_structural_coverage_met(ctx)
            ):
                state.messages.append(
                    AgentMessage.user(
                        "<system-reminder>当前 Evidence 已覆盖预设的最低结构要求。"
                        "请结合用户问题和实际数据判断内容是否足以回答：如果足够，"
                        "请调用 finish_research 提交结论；如果不足，请明确说明缺口并"
                        "生成能够补齐该缺口的下一步工具调用。</system-reminder>"
                    )
                )
                structural_coverage_reminded = True

            # 3. ---- 一轮推理：受控上下文 -> decide(profile=research) ----
            turn_index = ctx.iteration + 1
            step = agent_run_repository.start_step(
                self._session, self._run_row, turn_index
            )
            self._session.commit()
            profile = dataclass_replace(
                RESEARCH_PROFILE,
                working_state_builder=lambda _state: project_research_working_state(
                    ctx,
                    premise_result=ctx.premise_result,
                ),
            )
            try:
                decision = self._reasoner.decide(
                    state,
                    "research",
                    profile=profile,
                    step_id=getattr(step, "id", None),
                    step_index=turn_index,
                )
            except AgentCancellationRequested:
                agent_run_repository.cancel_step(
                    self._session, step, "用户在模型推理期间请求取消"
                )
                self._session.commit()
                return self._finalize_cancelled(ctx, turns, stage="during_model_call")
            except Exception as exc:  # noqa: BLE001 模型故障统一转为失败终态
                logger.warning("Research 模型推理失败", exc_info=True)
                agent_run_repository.fail_step(self._session, step, str(exc))
                return self._finalize_server_stop(
                    ctx,
                    turns,
                    stop_reason="model_failure",
                    reason_key="execution_failed",
                    extra_error=str(exc)[:500],
                )
            turns += 1
            ctx.consume_model_call()

            # ---- 直接自然语言回答不视为完成（§9.3.1）----
            if decision.is_direct_answer:
                stall_turns += 1
                state.messages.append(
                    AgentMessage.user(
                        "<system-reminder>纯文本回答不构成完成。请继续调用工具推进研究；若研究已经可以结束，必须调用 finish_research。</system-reminder>"
                    )
                )
                agent_run_repository.finish_step(
                    self._session,
                    step,
                    {"direct_answer": True, "turn": turns},
                )
                self._session.commit()
                ctx.advance_iteration(turn_index)
                if stall_turns >= self._config.research_max_stall_turns:
                    return self._finalize_server_stop(
                        ctx,
                        turns,
                        stop_reason="stalled",
                        reason_key="no_new_direction",
                    )
                continue

            # ---- 服务端批次规则校验（§9.3.5）----
            known_before = set(ctx.known_evidence_ids())
            valid_batch, reject_reason = self._validate_batch(ctx, decision.tool_calls)
            try:
                if not valid_batch:
                    observations = self._reject_batch(
                        step, decision.tool_calls, ctx, reject_reason
                    )
                    stall_turns += 1
                else:
                    observations = self._execute_batch(ctx, step, decision.tool_calls)
            except AgentCancellationRequested:
                agent_run_repository.cancel_step(
                    self._session, step, "用户在工具执行期间请求取消"
                )
                return self._finalize_cancelled(ctx, turns, stage="during_tool_call")
            except Exception as exc:  # noqa: BLE001 工具不可恢复失败转为失败终态
                logger.warning("Research 工具批次失败", exc_info=True)
                agent_run_repository.fail_step(self._session, step, str(exc))
                return self._finalize_server_stop(
                    ctx,
                    turns,
                    stop_reason="tool_failure",
                    reason_key="execution_failed",
                    extra_error=str(exc)[:500],
                )

            for _call, observation in observations:
                state.messages.append(
                    AgentMessage.tool(
                        self._observation_payload(
                            observation,
                            max_rows=ctx.budget.max_evidence_rows,
                        ),
                        observation.tool_call_id,
                    )
                )

            new_direction = any(
                observation.status is ToolObservationStatus.SUCCEEDED
                and set(observation.evidence_ids) - known_before
                for _call, observation in observations
            )
            actionable_failure = any(
                observation.status is not ToolObservationStatus.SUCCEEDED
                and (
                    observation.parameter_retryable
                    or observation.same_parameter_retryable
                )
                for _call, observation in observations
            )
            stall_turns = (
                0 if new_direction or actionable_failure else stall_turns + 1
            )

            agent_run_repository.finish_step(
                self._session,
                step,
                {
                    "turn": turns,
                    "tool_calls": [
                        {
                            "tool_call_id": observation.tool_call_id,
                            "tool_name": observation.tool_name,
                            "status": observation.status.value,
                        }
                        for _call, observation in observations
                    ],
                    "new_direction": new_direction,
                },
            )
            self._session.commit()
            ctx.advance_iteration(turn_index)

            # ---- 完成检查（§9.3.7）----
            if any(
                observation.tool_name == "finish_research"
                and observation.status is ToolObservationStatus.SUCCEEDED
                for _call, observation in observations
            ):
                return self._finalize_success(ctx, turns)

            if stall_turns >= self._config.research_max_stall_turns:
                return self._finalize_server_stop(
                    ctx,
                    turns,
                    stop_reason="stalled",
                    reason_key="no_new_direction",
                )

    def resume(self) -> ResearchAgentRunOutcome:
        """恢复一个中断的 Research Run 并继续循环（§8.4.5 / §9.3.2）。"""

        ctx, report = recover_research_run(
            self._session,
            self._run_row,
            semantic_runtime=self._semantic_runtime,
            compute_engine=self._compute_engine,
            result_store=self._result_store,
            cancellation=self._cancellation,
            trace_recorder=self._observation_recorder,
        )
        completion = ctx.completion
        if completion is not None:
            return ResearchAgentRunOutcome(
                completion=completion,
                stop_reason=(
                    "already_cancelled"
                    if completion.status == "cancelled"
                    else "already_finished"
                ),
                turns=0,
                recovery=report,
            )
        outcome = self.run(requirement=ctx.requirement, ctx=ctx)
        return dataclass_replace(outcome, recovery=report)

    # ------------------------------------------------------------------ #
    # 初始化与状态构造
    # ------------------------------------------------------------------ #

    def _initial_context(
        self,
        requirement: ResearchAgentRequirement,
    ) -> ResearchToolContext:
        agent_context = AgentToolContext(
            session=self._session,
            oid=int(self._run_row.oid),
            user_id=self._run_row.created_by,
            # ChatRecord 的字段名是 datasource（不是 datasource_id），
            # 与 orchestration/agent/state.py 的统一读法保持一致；
            # 读错字段会得到 None，让运行时边界校验报 DATASOURCE_SCOPE_MISMATCH。
            datasource_id=getattr(self._record, "datasource", None),
            execution_id=(
                f"agent:{self._run_row.id}" if self._run_row.id is not None else None
            ),
            chat_id=int(self._run_row.chat_id),
            record_id=int(self._run_row.record_id),
            dataset_id=getattr(self._record, "dataset_id", None),
            result_store=self._result_store,
            state={
                "research_run_id": requirement.run_id,
                RESEARCH_STATE_KEY: {},
                _RESULT_SETS_KEY: {},
                **self._context_state_overlay,
            },
        )
        ctx = ResearchToolContext(
            context=agent_context,
            requirement=requirement,
            semantic_runtime=self._semantic_runtime,
            compute_engine=self._compute_engine,
            budget=requirement.budget,
            cancellation=self._cancellation,
            trace_recorder=self._observation_recorder,
        )
        ctx.bind_to_context()
        return ctx

    def _persist_initial_state(self, ctx: ResearchToolContext) -> None:
        # 合并而不是整表替换：行上可能已有其他写入方落下的键（shadow 行的
        # ``shadow`` 标记、路由期遗留载荷）。run 1306 教训：曾整表覆盖把
        # shadow 标记抹掉，终态收口因找不到标记而静默跳过，留下孤儿 running 行。
        derived = dict(self._run_row.derived_state or {})
        derived[RESEARCH_STATE_KEY] = ctx.context.state[RESEARCH_STATE_KEY]
        derived[_RESULT_SETS_KEY] = {}
        self._persist_analysis_evidence(derived, ctx)
        agent_run_repository.update_run(
            self._session, self._run_row, derived_state=derived
        )
        self._session.commit()

    def _build_runtime_state(self, ctx: ResearchToolContext) -> AgentRuntimeState:
        return AgentRuntimeState(
            run=self._run_row,
            record=self._record,
            context=ctx.context,
            messages=[],
            budget=BudgetGuard(
                max_steps=self._config.max_steps,
                token_budget=self._config.token_budget,
                repeat_fuse_threshold=self._config.repeat_fuse_threshold,
                timeout_seconds=self._config.timeout_seconds,
            ),
            cancellation=self._cancellation or NeverCancelled(),
            system=AgentMessage.system(build_research_system_context(ctx.requirement)),
        )

    # ------------------------------------------------------------------ #
    # 前提确认（§9.3.3）
    # ------------------------------------------------------------------ #

    def _execute_initial_plan(
        self,
        ctx: ResearchToolContext,
        turns: int,
        started_at: float,
    ) -> ResearchAgentRunOutcome | None:
        """执行冻结首轮计划；节点全部尝试后才允许进入模型循环。"""

        plan = ctx.requirement.initial_plan
        if plan is None:
            return None
        if ctx.initial_plan_complete():
            return None

        batches = sorted({node.batch_index for node in plan.nodes})
        for batch_index in batches:
            if self._is_cancelled():
                return self._finalize_cancelled(ctx, turns, stage="initial_plan")
            exhaustion = self._budget_exhaustion(ctx, started_at)
            if exhaustion is not None:
                return self._finalize_server_stop(
                    ctx,
                    turns,
                    stop_reason="budget_exhausted",
                    reason_key="budget_exhausted",
                )

            pending = [
                node
                for node in plan.nodes
                if node.batch_index == batch_index
                and (ctx.initial_plan_node(node.node_id) or {}).get("status")
                not in {"succeeded", "failed"}
            ]
            if not pending:
                continue

            step_index = max(ctx.iteration + 1, batch_index)
            ctx.advance_iteration(step_index)
            step = agent_run_repository.start_step(
                self._session, self._run_row, step_index
            )
            self._session.commit()
            calls: list[ToolCall] = []
            call_nodes: dict[str, Any] = {}
            for node in pending:
                call_id = f"{ctx.run_id}:initial:{node.node_id}"
                previous = ctx.observation(call_id)
                if previous is not None:
                    ctx.mark_initial_plan_node(
                        node.node_id,
                        status=(
                            "succeeded"
                            if previous.status is ToolObservationStatus.SUCCEEDED
                            else "failed"
                        ),
                        tool_call_id=call_id,
                        evidence_ids=previous.evidence_ids,
                        message=previous.message,
                    )
                    continue
                args = self._initial_plan_call_args(ctx, node)
                if args is None:
                    ctx.mark_initial_plan_node(
                        node.node_id,
                        status="failed",
                        tool_call_id=f"{call_id}:skipped",
                        message="计划节点依赖的前序 Evidence 未生成。",
                    )
                    continue
                call = ToolCall(
                    name=(
                        "query_semantic_data"
                        if node.node_type == "query"
                        else "compute_evidence"
                    ),
                    args=args,
                    call_id=call_id,
                )
                calls.append(call)
                call_nodes[call_id] = node

            try:
                observations = self._execute_batch(ctx, step, calls) if calls else []
            except AgentCancellationRequested:
                agent_run_repository.cancel_step(
                    self._session, step, "用户在首轮计划执行期间请求取消"
                )
                return self._finalize_cancelled(ctx, turns, stage="initial_plan")
            except Exception as exc:  # noqa: BLE001 首轮工具失败转为失败终态
                logger.warning("Research 首轮计划执行失败", exc_info=True)
                agent_run_repository.fail_step(self._session, step, str(exc))
                return self._finalize_server_stop(
                    ctx,
                    turns,
                    stop_reason="tool_failure",
                    reason_key="execution_failed",
                    extra_error=str(exc)[:500],
                )

            for call, observation in observations:
                node = call_nodes[call.call_id]
                ctx.mark_initial_plan_node(
                    node.node_id,
                    status=(
                        "succeeded"
                        if observation.status is ToolObservationStatus.SUCCEEDED
                        else "failed"
                    ),
                    tool_call_id=call.call_id,
                    evidence_ids=observation.evidence_ids,
                    message=observation.message,
                )
            agent_run_repository.finish_step(
                self._session,
                step,
                {
                    "initial_plan": True,
                    "batch_index": batch_index,
                    "nodes": [node.node_id for node in pending],
                },
            )
            self._persist_plan_state(ctx)
            if batch_index == 0:
                outcome = self._initial_premise_check(ctx, turns)
                if outcome is not None:
                    return outcome

        if not ctx.initial_plan_exhausted():
            return self._finalize_server_stop(
                ctx,
                turns,
                stop_reason="initial_plan_failure",
                reason_key="execution_failed",
                extra_error="首轮基础计划尚未全部尝试完成，暂不进入动态 Replan。",
            )

        return self._initial_premise_check(ctx, turns)

    def _initial_premise_check(
        self,
        ctx: ResearchToolContext,
        turns: int,
    ) -> ResearchAgentRunOutcome | None:
        """首轮前提节点完成后立即判定，避免继续执行无效批次。"""

        premise = ctx.requirement.premise_to_verify
        if premise is None or ctx.premise_result is not None:
            return None
        evidence_ids = ctx.initial_plan_node_evidence_ids("premise-confirmation")
        premise_evidence = ctx.evidence(evidence_ids[0]) if evidence_ids else None
        verdict, observed = evaluate_premise_verdict(premise, premise_evidence)
        ctx.set_premise_result(
            {
                "status": verdict,
                "metric_ref": premise.metric_ref,
                "expected_direction": premise.expected_direction.value,
                "observed_direction": observed,
                "evidence_id": (
                    premise_evidence.evidence_id
                    if premise_evidence is not None
                    else None
                ),
            }
        )
        self._persist_plan_state(ctx)
        if verdict != "not_supported":
            return None
        completion = ResearchCompletion(
            run_id=ctx.run_id,
            status="succeeded",
            reason=ResearchCompletionReason.PREMISE_NOT_SUPPORTED,
            summary=(
                f"前提不成立：{premise.metric_ref} 实际方向为 {observed}，"
                f"与预期 {premise.expected_direction.value} 不符，研究提前结束。"
            ),
            evidence_ids=(
                (premise_evidence.evidence_id,) if premise_evidence is not None else ()
            ),
            limitations=("premise_not_supported",),
        )
        ctx.finish(completion)
        snapshot = self._persist_final_state(
            ctx,
            final_report=_report_json(self._final_report_payload(ctx, completion)),
        )
        return ResearchAgentRunOutcome(
            completion=completion,
            stop_reason="premise_not_supported",
            turns=turns,
            snapshot=snapshot,
        )

    def _initial_plan_call_args(
        self,
        ctx: ResearchToolContext,
        node: Any,
    ) -> dict[str, Any] | None:
        """把计划节点转换成受工具契约约束的参数。"""

        if node.node_type == "query":
            return {
                "metrics": list(node.metrics),
                "dimensions": list(node.dimensions),
                "time_ranges": [item.value for item in node.time_ranges],
                "filters": [item.model_dump(mode="json") for item in node.filters],
                "comparison": node.comparison.value,
                "analysis": node.analysis,
                "limit": 100,
                "purpose": node.purpose,
            }
        evidence_ids: list[str] = []
        for dependency in node.dependency_node_ids:
            dependency_evidence = ctx.initial_plan_node_evidence_ids(dependency)
            if not dependency_evidence:
                return None
            evidence_ids.append(dependency_evidence[0])
        return {
            "operation": node.compute_operation.value,
            "input_evidence_ids": evidence_ids,
            "metric_refs": list(node.metrics),
            "dimension_refs": list(node.dimensions),
            "group_by_refs": list(node.group_by_refs),
            "tolerance": node.tolerance,
            "purpose": node.purpose,
        }

    def _persist_plan_state(self, ctx: ResearchToolContext) -> None:
        """保存首轮节点状态，同时保留已有结果集和其他运行字段。"""

        derived = dict(self._run_row.derived_state or {})
        research_state = ctx.context.state.get(RESEARCH_STATE_KEY)
        if isinstance(research_state, dict):
            derived[RESEARCH_STATE_KEY] = research_state
        result_sets = ctx.context.state.get(_RESULT_SETS_KEY)
        if isinstance(result_sets, dict):
            derived[_RESULT_SETS_KEY] = {
                **dict(derived.get(_RESULT_SETS_KEY) or {}),
                **result_sets,
            }
        self._persist_analysis_evidence(derived, ctx)
        agent_run_repository.update_run(
            self._session,
            self._run_row,
            derived_state=derived,
        )
        self._session.commit()

    def _run_premise_preflight(
        self,
        ctx: ResearchToolContext,
        turns: int,
    ) -> ResearchAgentRunOutcome | None:
        """确定性验证前提；不成立直接进入 PREMISE_NOT_SUPPORTED 报告。

        返回 None 表示前提已成立或无法判定，循环继续；否则返回终态结果。
        """

        premise = ctx.requirement.premise_to_verify
        if premise is None:
            return None
        preflight_index = ctx.iteration + 1
        step = agent_run_repository.start_step(
            self._session, self._run_row, preflight_index
        )
        self._session.commit()
        call = ToolCall(
            name="query_semantic_data",
            args=dict(build_premise_query_args(ctx.requirement) or {}),
            call_id=f"{ctx.run_id}:premise-preflight",
        )
        try:
            _call, observation = self._execute_batch(ctx, step, [call])[0]
        except Exception as exc:  # noqa: BLE001 前提查询不可恢复失败按执行失败收口
            logger.warning("Research 前提查询失败", exc_info=True)
            agent_run_repository.fail_step(self._session, step, str(exc))
            return self._finalize_server_stop(
                ctx,
                turns,
                stop_reason="model_failure",
                reason_key="execution_failed",
                extra_error=str(exc)[:500],
            )
        evidence = (
            ctx.evidence(observation.evidence_ids[0])
            if observation.evidence_ids
            else None
        )
        verdict, observed = evaluate_premise_verdict(premise, evidence)
        ctx.set_premise_result(
            {
                "status": verdict,
                "metric_ref": premise.metric_ref,
                "expected_direction": premise.expected_direction.value,
                "observed_direction": observed,
                "evidence_id": (
                    observation.evidence_ids[0] if observation.evidence_ids else None
                ),
            }
        )
        ctx.advance_iteration(preflight_index)
        agent_run_repository.finish_step(
            self._session,
            step,
            {"premise_preflight": verdict, "turn": turns},
        )
        self._persist_final_state(ctx)
        if verdict != "not_supported":
            return None
        completion = ResearchCompletion(
            run_id=ctx.run_id,
            status="succeeded",
            reason=ResearchCompletionReason.PREMISE_NOT_SUPPORTED,
            summary=(
                f"前提不成立：{premise.metric_ref} 实际方向为 {observed}，"
                f"与预期 {premise.expected_direction.value} 不符，研究提前结束。"
            ),
            evidence_ids=tuple(observation.evidence_ids[:_MAX_COMPLETION_EVIDENCE_IDS]),
            limitations=("premise_not_supported",),
        )
        ctx.finish(completion)
        snapshot = self._persist_final_state(
            ctx,
            final_report=_report_json(self._final_report_payload(ctx, completion)),
        )
        return ResearchAgentRunOutcome(
            completion=completion,
            stop_reason="premise_not_supported",
            turns=turns,
            snapshot=snapshot,
        )

    # ------------------------------------------------------------------ #
    # 批次规则与执行（§9.3.5）
    # ------------------------------------------------------------------ #

    def _validate_batch(
        self,
        ctx: ResearchToolContext,
        calls: list[ToolCall],
    ) -> tuple[bool, str | None]:
        """校验一轮工具选择的协议规则；违规整批拒绝，不做部分执行。"""

        allowlist = set(RESEARCH_TOOL_NAMES)
        unknown = sorted({call.name for call in calls} - allowlist)
        if unknown:
            return False, (
                f"调用了未声明的研究工具：{', '.join(unknown)}。"
                f"可用工具：{', '.join(sorted(allowlist))}。"
            )
        if len(calls) > 1 and any(call.name == "finish_research" for call in calls):
            return False, "finish_research 必须单独一轮提交，不能和其他工具同批。"
        known = set(ctx.known_evidence_ids())
        for call in calls:
            if call.name == "finish_research":
                # finish 引用的证据是结论引用而非执行依赖，不受分轮规则限制。
                continue
            missing = [
                evidence_id
                for evidence_id in self._referenced_evidence_ids(call)
                if evidence_id not in known
            ]
            if missing:
                return False, (
                    f"同批工具引用了尚不存在的证据 {', '.join(missing)}；"
                    "依赖其他工具输出的调用必须拆到下一轮。"
                )
        return True, None

    def _referenced_evidence_ids(self, call: ToolCall) -> list[str]:
        args = call.args if isinstance(call.args, dict) else {}
        if call.name == "inspect_evidence":
            value = args.get("evidence_id")
            return [value] if isinstance(value, str) and value else []
        if call.name == "compute_evidence":
            values = args.get("input_evidence_ids")
            if isinstance(values, (list, tuple)):
                return [item for item in values if isinstance(item, str) and item]
        return []

    def _execute_batch(
        self,
        ctx: ResearchToolContext,
        step: Any,
        calls: list[ToolCall],
    ) -> list[tuple[ToolCall, ToolObservation]]:
        """并行执行独立调用，主线程按原顺序合并并持久化研究事实。"""

        if len(calls) <= 1:
            return [self._execute_single(ctx, step, call) for call in calls]
        query_count = sum(call.name == "query_semantic_data" for call in calls)
        remaining_queries = max(
            ctx.budget.max_queries - ctx.budget_usage().queries,
            0,
        )
        call_fingerprints = [
            (call.name, orjson.dumps(call.args or {}, option=orjson.OPT_SORT_KEYS))
            for call in calls
        ]
        if query_count > remaining_queries or len(set(call_fingerprints)) < len(
            call_fingerprints
        ):
            # 预算不足或同批存在重复请求时保持顺序执行，让工具自身的预算门和
            # 指纹去重看到前一个调用已经提交的状态。
            return [self._execute_single(ctx, step, call) for call in calls]

        commits = [
            ResearchToolCallCommit(
                self._session,
                self._run_row,
                step_id=int(step.id),
                tool_call_id=call.call_id,
                tool_name=call.name,
                args_summary=dict(call.args or {}),
                ctx=ctx,
            )
            for call in calls
        ]
        for commit in commits:
            commit.__enter__()

        worker_contexts: dict[str, ResearchToolContext] = {}
        baseline_usage = ctx.budget_usage()

        def execute_isolated(call: ToolCall) -> ToolResult[Any]:
            if hasattr(self._session, "get_bind"):
                worker_session: Any = Session(bind=self._session.get_bind())
            else:
                # 内存测试 Session 也必须按调用隔离，不能在线程间共享同一实例。
                worker_session = type(self._session)(self._run_row)
            session_scope = (
                worker_session
                if hasattr(worker_session, "__enter__")
                else nullcontext(worker_session)
            )
            with session_scope:
                worker_agent_context = dataclass_replace(
                    ctx.context,
                    session=worker_session,
                    state=deepcopy(ctx.context.state),
                )
                semantic_runtime = self._semantic_runtime
                if semantic_runtime is not None and hasattr(
                    semantic_runtime, "fork_with_session"
                ):
                    semantic_runtime = semantic_runtime.fork_with_session(
                        worker_session
                    )
                worker_ctx = dataclass_replace(
                    ctx,
                    context=worker_agent_context,
                    semantic_runtime=semantic_runtime,
                    trace_recorder=None,
                )
                result = self._registry.execute(call, worker_ctx)
                worker_contexts[call.call_id] = worker_ctx
                return result

        try:
            executed = execute_tool_batch(
                calls,
                execute_isolated,
                max_workers=int(getattr(self._config, "tool_parallel_workers", 4) or 4),
                cancellation=self._cancellation,
            )
        except ToolBatchExecutionError as exc:
            self._finish_parallel_commits(
                ctx,
                commits,
                exc.outcomes,
                worker_contexts,
                baseline_usage,
            )
            raise exc.cause from exc

        return self._finish_parallel_commits(
            ctx,
            commits,
            executed,
            worker_contexts,
            baseline_usage,
        )

    def _finish_parallel_commits(
        self,
        ctx: ResearchToolContext,
        commits: list[ResearchToolCallCommit],
        outcomes: Sequence[tuple[ToolCall, ToolResult[Any] | BaseException]],
        worker_contexts: dict[str, ResearchToolContext],
        baseline_usage: ResearchBudgetUsage,
    ) -> list[tuple[ToolCall, ToolObservation]]:
        """合并隔离状态，并按调用原顺序提交每个工具事实。"""

        observations: list[tuple[ToolCall, ToolObservation]] = []
        for commit, (call, outcome) in zip(commits, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                commit.__exit__(type(outcome), outcome, outcome.__traceback__)
                continue
            worker_ctx = worker_contexts.get(call.call_id)
            if worker_ctx is not None and outcome.status is ToolStatus.SUCCEEDED:
                self._merge_parallel_context(ctx, worker_ctx, baseline_usage)
            observation = commit.finish(outcome)
            commit.__exit__(None, None, None)
            if observation is None:
                raise TypeError("RESEARCH_HARNESS_OBSERVATION_REQUIRED")
            observations.append((call, observation))
        return observations

    @staticmethod
    def _merge_parallel_context(
        ctx: ResearchToolContext,
        worker_ctx: ResearchToolContext,
        baseline_usage: ResearchBudgetUsage,
    ) -> None:
        """只合并工具允许产生的状态增量，避免覆盖其他并行调用。"""

        for evidence in worker_ctx.evidences():
            ctx.register_evidence(evidence)
        EvidenceRegistry(ctx.context.state).merge(worker_ctx.analysis_evidences())

        worker_research = worker_ctx.context.state.get(RESEARCH_STATE_KEY)
        main_research = ctx.context.state.get(RESEARCH_STATE_KEY)
        if not isinstance(worker_research, dict) or not isinstance(main_research, dict):
            raise TypeError("RESEARCH_TOOL_STATE_INVALID")
        worker_fingerprints = worker_research.get("fingerprints")
        main_fingerprints = main_research.setdefault("fingerprints", {})
        if isinstance(worker_fingerprints, dict) and isinstance(
            main_fingerprints, dict
        ):
            main_fingerprints.update(worker_fingerprints)

        worker_results = worker_ctx.context.state.get(_RESULT_SETS_KEY)
        main_results = ctx.context.state.setdefault(_RESULT_SETS_KEY, {})
        if isinstance(worker_results, dict) and isinstance(main_results, dict):
            main_results.update(worker_results)

        worker_usage = worker_ctx.budget_usage()
        current_usage = ctx.budget_usage()
        main_research["budget_usage"] = ResearchBudgetUsage(
            queries=current_usage.queries
            + max(worker_usage.queries - baseline_usage.queries, 0),
            model_calls=current_usage.model_calls,
            duration_seconds=current_usage.duration_seconds,
            evidence_rows=current_usage.evidence_rows,
            evidence_chars=current_usage.evidence_chars,
        ).model_dump(mode="json")
        observation = worker_ctx.observations()[-1]
        ctx.record_observation(observation)

    def _execute_single(
        self,
        ctx: ResearchToolContext,
        step: Any,
        call: ToolCall,
    ) -> tuple[ToolCall, ToolObservation]:
        with ResearchToolCallCommit(
            self._session,
            self._run_row,
            step_id=int(step.id),
            tool_call_id=call.call_id,
            tool_name=call.name,
            args_summary=dict(call.args or {}),
            ctx=ctx,
        ) as commit:
            result = self._registry.execute(call, ctx)
            observation = commit.finish(result)
        if observation is None:
            # 研究工具成功必产出观察；到这里说明注册表返回了非研究结果。
            raise TypeError("RESEARCH_HARNESS_OBSERVATION_REQUIRED")
        return call, observation

    def _reject_batch(
        self,
        step: Any,
        calls: list[ToolCall],
        ctx: ResearchToolContext,
        reason: str | None,
    ) -> list[tuple[ToolCall, ToolObservation]]:
        """把违规批次落成结构化失败事实，不执行任何工具。"""

        message = reason or "工具批次违反研究协议。"
        results: list[tuple[ToolCall, ToolObservation]] = []
        for call in calls:
            with ResearchToolCallCommit(
                self._session,
                self._run_row,
                step_id=int(step.id),
                tool_call_id=call.call_id,
                tool_name=call.name,
                args_summary=dict(call.args or {}),
                ctx=ctx,
            ) as commit:
                rejection = ToolResult.rejected(
                    message,
                    error_code="batch_rule_violation",
                    error_category=ToolErrorCategory.VALIDATION,
                )
                observation = commit.finish(rejection)
            if observation is None:
                raise TypeError("RESEARCH_HARNESS_REJECTION_OBSERVATION_REQUIRED")
            results.append((call, observation))
        return results

    def _observation_payload(
        self,
        observation: ToolObservation,
        *,
        max_rows: int,
    ) -> str:
        """把观察投影成 Research 协议的 Tool 消息正文（有界）。"""

        failed = observation.status is not ToolObservationStatus.SUCCEEDED
        payload: dict[str, Any] = {
            "tool_call_id": observation.tool_call_id,
            "tool_name": observation.tool_name,
            "status": observation.status.value,
            "message": observation.message,
            "evidence_ids": list(observation.evidence_ids),
            "statistics": observation.statistics,
            "limitations": list(observation.limitations),
            "sample_rows": list(observation.sample_rows)[:max_rows],
        }
        if failed:
            payload["error"] = {
                "code": (
                    observation.error_code.value if observation.error_code else None
                ),
                "stage": (
                    observation.failure_stage.value
                    if observation.failure_stage
                    else None
                ),
                "retryable": observation.retryable,
                "parameter_retryable": observation.parameter_retryable,
                "same_parameter_retryable": observation.same_parameter_retryable,
                "suggested_corrections": list(observation.suggested_corrections),
                # 校验器已经生成了可执行的违规明细；必须反馈给模型，
                # 否则模型只能用相同参数盲目重试，最终触发停滞收口。
                "details": observation.details,
            }
        return orjson.dumps(payload).decode()

    # ------------------------------------------------------------------ #
    # 停止条件与终态（§9.3.7）
    # ------------------------------------------------------------------ #

    def _is_cancelled(self) -> bool:
        signal = self._cancellation
        return bool(signal is not None and signal.is_cancelled())

    def _budget_exhaustion(
        self,
        ctx: ResearchToolContext,
        started_at: float,
    ) -> str | None:
        """服务端预算轴检查；查询轴由工具层强制，这里兜底模型/迭代/时间。"""

        usage = ctx.budget_usage()
        budget = ctx.budget
        if ctx.iteration >= budget.max_iterations:
            return "iterations"
        if usage.model_calls >= budget.max_model_calls:
            return "model_calls"
        elapsed = time.monotonic() - started_at
        if elapsed >= budget.max_duration_seconds:
            return "duration"
        return None

    def _minimum_structural_coverage_met(self, ctx: ResearchToolContext) -> bool:
        """判断最低结构覆盖；该结果不能证明内容足以回答用户问题。

        额外要求台账里至少有一条证据：空台账上的“满足”没有研究价值，
        结构覆盖提示必须锚定在真实进展之后。
        """

        if not ctx.analysis_evidences():
            return False
        return evaluate_completion(
            ctx.requirement,
            ctx.analysis_evidences(),
            premise_result=ctx.premise_result,
        ).minimum_requirements_met

    def _server_completion(
        self,
        ctx: ResearchToolContext,
        reason_key: str,
        *,
        extra_error: str | None = None,
    ) -> ResearchCompletion:
        evidences = ctx.evidences()
        evidence_ids = tuple(
            item.evidence_id for item in evidences[:_MAX_COMPLETION_EVIDENCE_IDS]
        )
        usage = ctx.budget_usage()
        if reason_key == "budget_exhausted":
            return ResearchCompletion(
                run_id=ctx.run_id,
                status="partial",
                reason=ResearchCompletionReason.BUDGET_EXHAUSTED,
                summary=(
                    f"研究因预算耗尽结束：已完成 {ctx.iteration} 轮、"
                    f"{usage.queries} 次查询、{usage.model_calls} 次模型调用，"
                    f"保留 {len(evidences)} 条证据。"
                ),
                evidence_ids=evidence_ids,
                limitations=("budget_exhausted",),
            )
        if reason_key == "execution_failed":
            return ResearchCompletion(
                run_id=ctx.run_id,
                status="failed",
                reason=ResearchCompletionReason.EXECUTION_FAILED,
                summary=(
                    extra_error
                    or "研究因不可恢复的执行失败而结束，已有证据被完整保留。"
                )[:4000],
                evidence_ids=evidence_ids,
                limitations=("execution_failed",),
            )
        if evidences:
            return ResearchCompletion(
                run_id=ctx.run_id,
                status="partial",
                reason=ResearchCompletionReason.PARTIAL_FAILURE,
                summary=(
                    f"连续多轮没有产生新方向，研究在第 {ctx.iteration} 轮结束；"
                    f"共保留 {len(evidences)} 条证据供后续分析。"
                ),
                evidence_ids=evidence_ids,
                limitations=("no_new_direction",),
            )
        return ResearchCompletion(
            run_id=ctx.run_id,
            status="failed",
            reason=ResearchCompletionReason.DATA_INSUFFICIENT,
            summary=(
                f"连续多轮没有产生新方向且没有任何证据，研究在第 "
                f"{ctx.iteration} 轮结束。"
            ),
            limitations=("data_insufficient",),
        )

    def _final_report_payload(
        self,
        ctx: ResearchToolContext,
        completion: ResearchCompletion,
    ) -> dict[str, Any]:
        """finish 通过校验后的最终报告；报告输入由 finish 工具暂存。"""

        inputs = ctx.report_inputs() or {}
        return build_final_report(
            ctx,
            completion,
            findings=tuple(inputs.get("findings") or ()),
            claims=tuple(inputs.get("claims") or ()),
            assessments=ctx.hypothesis_assessments(),
        )

    def _finalize_success(
        self,
        ctx: ResearchToolContext,
        turns: int,
    ) -> ResearchAgentRunOutcome:
        completion = ctx.completion
        if completion is None:
            raise TypeError("RESEARCH_HARNESS_COMPLETION_MISSING")
        snapshot = self._persist_final_state(
            ctx,
            final_report=_report_json(self._final_report_payload(ctx, completion)),
        )
        return ResearchAgentRunOutcome(
            completion=completion,
            stop_reason="finished",
            turns=turns,
            snapshot=snapshot,
        )

    def _finalize_server_stop(
        self,
        ctx: ResearchToolContext,
        turns: int,
        *,
        stop_reason: str,
        reason_key: str,
        extra_error: str | None = None,
    ) -> ResearchAgentRunOutcome:
        completion = ctx.completion or self._server_completion(
            ctx,
            reason_key,
            extra_error=extra_error,
        )
        if not ctx.finished:
            ctx.finish(completion)
        partial = build_partial_report(ctx, completion, stop_reason=stop_reason)
        snapshot = self._persist_final_state(
            ctx,
            report_draft=_report_json(partial),
        )
        return ResearchAgentRunOutcome(
            completion=completion,
            stop_reason=stop_reason,
            turns=turns,
            snapshot=snapshot,
        )

    def _finalize_cancelled(
        self,
        ctx: ResearchToolContext,
        turns: int,
        *,
        stage: str,
    ) -> ResearchAgentRunOutcome:
        completion = ctx.completion
        if completion is None:
            # 仅用于构建部分报告的受限完成态；终态写入仍由取消收口负责。
            completion = ResearchCompletion(
                run_id=ctx.run_id,
                status="cancelled",
                reason=ResearchCompletionReason.CANCELLED,
                summary=f"研究在 {stage} 阶段被取消；已确认证据保留在台账中。",
            )
        snapshot = cancel_research_run(
            self._session,
            self._run_row,
            ctx,
            stage=stage,
            report_draft=_report_json(
                build_partial_report(ctx, completion, stop_reason="cancelled")
            ),
        )
        completion = ctx.completion
        if completion is None:
            raise TypeError("RESEARCH_HARNESS_CANCELLED_COMPLETION_MISSING")
        return ResearchAgentRunOutcome(
            completion=completion,
            stop_reason="cancelled",
            turns=turns,
            snapshot=snapshot,
        )

    def _persist_final_state(
        self,
        ctx: ResearchToolContext,
        *,
        report_draft: str | None = None,
        final_report: str | None = None,
    ) -> ResearchRunSnapshot:
        """刷新快照并连同研究状态一次性持久化。"""

        derived = dict(self._run_row.derived_state or {})
        research_state = ctx.context.state.get(RESEARCH_STATE_KEY)
        if isinstance(research_state, dict):
            derived[RESEARCH_STATE_KEY] = research_state
        result_sets = ctx.context.state.get(_RESULT_SETS_KEY)
        if isinstance(result_sets, dict):
            derived[_RESULT_SETS_KEY] = {
                **dict(derived.get(_RESULT_SETS_KEY) or {}),
                **result_sets,
            }
        self._persist_analysis_evidence(derived, ctx)
        snapshot = build_research_run_snapshot(
            ctx,
            running_tool_call_ids=[],
            agent_run_id=self._run_row.id,
            premise_result=ctx.premise_result,
            report_draft=report_draft,
            final_report=final_report,
        )
        derived["research_run_snapshot"] = snapshot.model_dump(mode="json")
        agent_run_repository.update_run(
            self._session, self._run_row, derived_state=derived
        )
        self._session.commit()
        return snapshot

    @staticmethod
    def _persist_analysis_evidence(
        derived: dict[str, Any],
        ctx: ResearchToolContext,
    ) -> None:
        """把统一 Evidence 台账随 Research 状态一起持久化。"""

        registry = ctx.context.state.get(ANALYSIS_EVIDENCE_REGISTRY_KEY)
        if isinstance(registry, dict):
            derived[ANALYSIS_EVIDENCE_REGISTRY_KEY] = dict(registry)


__all__ = ["ResearchAgentHarness", "ResearchAgentRunOutcome"]
