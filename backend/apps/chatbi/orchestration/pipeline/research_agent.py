"""Research Agent Harness：Function Calling 动态研究循环（doc38 §9）。

取代旧 ``ResearchPolicyDecision`` / ``ResearchAction`` 计划式循环：模型每轮
根据 Requirement、Working State 和 Observation 自己选择四个研究工具，服务端
只负责协议规则、预算、停止条件和持久化边界。没有全局 DAG，也没有 Action
物化器；每一步事实都落在阶段 4 的 ``ResearchToolCallCommit`` 边界上，
可追踪、可恢复、可取消。

主流程集中在 :meth:`ResearchAgentHarness.run` 一个方法内（§9.3.2）；
Tool 执行、批次校验和终态收口等复杂职责下沉为私有方法。新路径当前只在
测试或 Shadow 场景运行（§9.2.7），不接入 RunOrchestrator 分发。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from typing import Any

import orjson

from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
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
from apps.chatbi.services.research.agent_context import (
    build_premise_query_args,
    build_research_system_context,
    evaluate_premise_verdict,
    project_research_working_state,
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
)
from apps.trace import AgentTraceRecorder, DisabledAgentTraceRecorder

logger = logging.getLogger(__name__)

_RESULT_SETS_KEY = "result_sets"
_MAX_COMPLETION_EVIDENCE_IDS = 20


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
            ctx = self._initial_context(requirement)
            self._persist_initial_state(ctx)
        state = self._build_runtime_state(ctx)

        started_at = time.monotonic()
        stall_turns = 0
        requirements_directive_at: int | None = None
        turns = 0

        while True:
            if self._is_cancelled():
                return self._finalize_cancelled(ctx, turns, stage="loop_top")

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
                ctx.requirement.premise_to_verify is not None
                and ctx.premise_result is None
            ):
                outcome = self._run_premise_preflight(ctx, turns)
                if outcome is not None:
                    return outcome

            # ---- 证据需求满足后提醒收口；宽限期过后由服务端强制停止 ----
            if self._requirements_satisfied(ctx):
                if requirements_directive_at is None:
                    state.messages.append(
                        AgentMessage.user(
                            "<system-reminder>证据需求已全部满足。请尽快调用 "
                            "finish_research 提交结论。</system-reminder>"
                        )
                    )
                    requirements_directive_at = turns
                elif (
                    turns
                    >= requirements_directive_at + self._config.research_max_stall_turns
                ):
                    return self._finalize_server_stop(
                        ctx,
                        turns,
                        stop_reason="requirements_unanswered",
                        reason_key="no_new_direction",
                    )

            # ---- 一轮推理：受控上下文 -> decide(profile=research) ----
            turn_index = ctx.iteration + 1
            step = agent_run_repository.start_step(self._session, self._run_row, turn_index)
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
                        "<system-reminder>纯文本回答不构成完成。请继续调用工具推进研究；"
                        "若研究已经可以结束，必须调用 finish_research。</system-reminder>"
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
                    observations = self._execute_batch(
                        ctx, step, decision.tool_calls
                    )
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
            stall_turns = 0 if new_direction else stall_turns + 1

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
            datasource_id=None,
            execution_id=(
                f"agent:{self._run_row.id}" if self._run_row.id is not None else None
            ),
            chat_id=int(self._run_row.chat_id),
            record_id=int(self._run_row.record_id),
            dataset_id=None,
            result_store=self._result_store,
            state={
                "research_run_id": requirement.run_id,
                RESEARCH_STATE_KEY: {},
                _RESULT_SETS_KEY: {},
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
        derived = {
            RESEARCH_STATE_KEY: ctx.context.state[RESEARCH_STATE_KEY],
            _RESULT_SETS_KEY: {},
        }
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
        step = agent_run_repository.start_step(self._session, self._run_row, preflight_index)
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
        snapshot = self._persist_final_state(ctx)
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
        """在提交边界内执行一批独立工具调用；每个调用独立持久化。"""

        workers = max(1, min(self._config.tool_parallel_workers, len(calls)))
        if workers <= 1:
            return [self._execute_single(ctx, step, call) for call in calls]
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="research-tool",
        ) as pool:
            futures = [
                pool.submit(self._execute_single, ctx, step, call) for call in calls
            ]
            return [future.result() for future in futures]

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
                "same_parameter_retryable": observation.same_parameter_retryable,
                "suggested_corrections": list(observation.suggested_corrections),
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

    def _requirements_satisfied(self, ctx: ResearchToolContext) -> bool:
        requirement = ctx.requirement
        evidences = ctx.evidences()
        for req in requirement.evidence_requirements:
            required = set(req.required_asset_refs)
            covered = sum(
                1
                for item in evidences
                if not required
                or required & (set(item.metric_refs) | set(item.dimension_refs))
            )
            if covered < req.minimum_count:
                return False
        if (
            requirement.premise_to_verify is not None
            and ctx.premise_result is None
        ):
            return False
        return True

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
                status="succeeded",
                reason=ResearchCompletionReason.NO_NEW_DIRECTION,
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

    def _finalize_success(
        self,
        ctx: ResearchToolContext,
        turns: int,
    ) -> ResearchAgentRunOutcome:
        completion = ctx.completion
        if completion is None:
            raise TypeError("RESEARCH_HARNESS_COMPLETION_MISSING")
        snapshot = self._persist_final_state(ctx)
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
        snapshot = self._persist_final_state(ctx)
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
        snapshot = cancel_research_run(
            self._session,
            self._run_row,
            ctx,
            stage=stage,
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
        snapshot = build_research_run_snapshot(
            ctx,
            running_tool_call_ids=[],
            agent_run_id=self._run_row.id,
            premise_result=ctx.premise_result,
        )
        derived["research_run_snapshot"] = snapshot.model_dump(mode="json")
        agent_run_repository.update_run(
            self._session, self._run_row, derived_state=derived
        )
        self._session.commit()
        return snapshot


__all__ = ["ResearchAgentHarness", "ResearchAgentRunOutcome"]
