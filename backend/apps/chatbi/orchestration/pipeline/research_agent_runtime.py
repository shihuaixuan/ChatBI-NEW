"""Evidence 驱动 Research Agent 的多轮 ReAct Runtime。

本模块只负责 Research Run 的生命周期和循环控制。研究判断由模型通过
``ResearchTurnDecision`` 提交，查询、计算、证据读取和结束校验由新的
``ResearchToolRuntime`` 执行；主循环只读写 ResearchState。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from queue import Empty, Queue
from threading import Event, Thread
from typing import Any, cast

import orjson

from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.models.dto.research_agent import (
    Completion,
    CompletionLimitation,
    Evidence,
    RemainingBudget,
    ResearchAction,
    ResearchActionType,
    ResearchAgentInput,
    ResearchAgentRequirement,
    ResearchStateSnapshot,
    ResearchStateStatus,
    SemanticContext,
    SemanticDimension,
    SemanticMetric,
    ToolErrorCode,
    ToolResult,
    ToolResultStatus,
)
from apps.chatbi.models.orm.agent_run import AgentToolCallStatus
from apps.chatbi.orchestration.agent.cancellation import (
    AgentCancellationRequested,
    CancellationStage,
)
from apps.chatbi.orchestration.agent.messages import AgentMessage, restore_messages
from apps.chatbi.orchestration.agent.reasoning import (
    AgentReasoner,
    ResearchDecisionParseError,
)
from apps.chatbi.orchestration.agent.reasoning_profile import (
    RESEARCH_REACT_PROFILE,
    ReasoningProfile,
)
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.evidence import ANALYSIS_EVIDENCE_REGISTRY_KEY
from apps.chatbi.services.research.action_fingerprint import (
    research_action_fingerprint,
)
from apps.chatbi.services.research.agent_context import (
    build_research_system_context,
    project_research_react_state,
)
from apps.chatbi.services.research.run_lifecycle import (
    RESEARCH_STATE_KEY,
    ResearchRecoveryReport,
    recover_research_agent_run,
)
from apps.chatbi.services.research.runtime import (
    ResearchToolRegistry,
    ResearchToolRuntime,
)
from apps.chatbi.services.research.state_snapshot import (
    RESEARCH_STATE_SNAPSHOT_KEY,
    build_research_state_snapshot,
)
from apps.chatbi.services.research.tool_context import ResearchToolContext
from apps.chatbi.services.research.tools import build_research_tool_registry
from apps.tool import BudgetGuard, NeverCancelled
from apps.trace import (
    AgentTraceRecorder,
    DisabledAgentTraceRecorder,
    TraceNodeSpec,
    TraceNodeStatus,
    TraceNodeType,
    tool_attributes,
)

logger = logging.getLogger(__name__)

_RESULT_SETS_KEY = "result_sets"
_MESSAGES_KEY = "research_react_messages"
_SNAPSHOT_KEY = RESEARCH_STATE_SNAPSHOT_KEY
_MAX_COMPLETION_EVIDENCE_IDS = 20


class ResearchRuntimeInterrupted(RuntimeError):
    """测试用进程中断信号；运行时不把它转换为业务失败。"""


@dataclass(frozen=True)
class ResearchRuntimeTermination:
    """运行时因取消或系统错误产生的终止结果。"""

    status: str
    reason: str
    summary: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResearchAgentRunOutcome:
    """一次 Research Agent 执行的结果。"""

    completion: Completion | ResearchRuntimeTermination | None
    stop_reason: str
    turns: int
    snapshot: ResearchStateSnapshot | None = None
    recovery: ResearchRecoveryReport | None = None
    evidences: tuple[Evidence, ...] = ()
    direct_answer: str | None = None


class ResearchAgentRuntime:
    """执行 ResearchState 驱动的完整多轮 ReAct 循环。"""

    def __init__(
        self,
        *,
        session: Any,
        config: AgentConfig,
        run_row: Any,
        model_client: Any,
        record: Any = None,
        semantic_runtime: Any = None,
        semantic_retrieval_service: Any = None,
        compute_engine: Any = None,
        result_store: Any = None,
        recorder: AgentTraceRecorder | None = None,
        cancellation: Any = None,
        observation_recorder: Any = None,
        context_state_overlay: Mapping[str, Any] | None = None,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self._config = config
        self._run_row = run_row
        self._record = record
        self._model_client = model_client
        self._semantic_runtime = semantic_runtime
        self._semantic_retrieval_service = semantic_retrieval_service
        self._compute_engine = compute_engine
        self._result_store = result_store
        self._cancellation = cancellation
        self._observation_recorder = observation_recorder
        self._context_state_overlay = dict(context_state_overlay or {})
        self._failure_injector = failure_injector
        self._registry: ResearchToolRegistry = build_research_tool_registry()
        self._recorder = recorder or DisabledAgentTraceRecorder()
        self._reasoner = AgentReasoner(
            config,
            model_client,
            cast(Any, self._registry),
            self._recorder,
        )
        self._restored_messages: list[AgentMessage] | None = None

    # ------------------------------------------------------------------ #
    # 公开入口
    # ------------------------------------------------------------------ #

    def run(
        self,
        *,
        requirement: ResearchAgentRequirement | None = None,
        ctx: ResearchToolContext | None = None,
    ) -> ResearchAgentRunOutcome:
        """初始化或继续一个 Research Run。"""

        if ctx is None:
            if requirement is None:
                raise TypeError("RESEARCH_AGENT_REQUIREMENT_REQUIRED")
            ctx = self._initial_context(requirement)
            self._persist_state(ctx, messages=[])

        if ctx.current_status is ResearchStateStatus.WAITING_FOR_USER:
            return ResearchAgentRunOutcome(
                None,
                "waiting_for_user",
                0,
                snapshot=self._build_snapshot(ctx),
            )
        if ctx.react_completion() is not None:
            return self._already_finished(ctx)

        state = self._build_runtime_state(ctx)
        started_at = time.monotonic()
        self._started_at = started_at
        turns = 0
        stall_turns = 0
        previous_failure_signature: tuple[Any, ...] | None = None
        previous_signature = self._progress_signature(ctx)

        while True:
            if self._is_cancelled(ctx):
                return self._finalize_cancelled(ctx, turns, "loop_top", state)

            if turns >= ctx.budget.max_iterations:
                return self._finalize_budget(ctx, turns, state, "iteration_budget")

            usage = ctx.react_budget_usage()
            if usage.model_turns >= ctx.budget.max_model_calls:
                return self._finalize_budget(ctx, turns, state, "model_budget")

            visible_tools = self._visible_tools(ctx, started_at)
            turn_index = ctx.next_step_index()
            step = agent_run_repository.start_step(
                self._session,
                self._run_row,
                turn_index,
            )
            self._session.commit()

            profile = self._profile(ctx, visible_tools)
            try:
                decision_result = self._reasoner.decide_research(
                    state,
                    profile=profile,
                    step_id=getattr(step, "id", None),
                    step_index=turn_index,
                )
                self._inject_failure("after_model_return")
            except AgentCancellationRequested:
                agent_run_repository.cancel_step(
                    self._session,
                    step,
                    "用户在 Research Agent 推理期间请求取消",
                )
                self._session.commit()
                return self._finalize_cancelled(
                    ctx,
                    turns,
                    "during_model_call",
                    state,
                )
            except ResearchRuntimeInterrupted:
                raise
            except ResearchDecisionParseError as exc:
                turns += 1
                ctx.consume_react_model_turn(exc.model_turns)
                stall_turns += 1
                self._append_model_feedback(
                    state,
                    f"ResearchTurnDecision 解析失败：{exc.code}。请提交合法动作。",
                )
                agent_run_repository.finish_step(
                    self._session,
                    step,
                    {"turn": turns, "decision_error": exc.code},
                )
                self._persist_state(ctx, messages=state.messages)
                if stall_turns >= self._config.research_max_stall_turns:
                    return self._finalize_stalled(ctx, turns, state)
                continue
            except Exception as exc:  # noqa: BLE001 - 系统错误显式收口为 failed
                logger.warning("Research Agent 模型调用失败", exc_info=True)
                agent_run_repository.fail_step(self._session, step, str(exc)[:2_000])
                self._session.commit()
                return self._finalize_failed(ctx, turns, state, str(exc))

            turns += 1
            ctx.consume_react_model_turn(1 + int(decision_result.repaired))

            if decision_result.is_direct_answer:
                answer = decision_result.response.content.strip()
                ctx.set_current_status(ResearchStateStatus.COMPLETED)
                self._persist_state(
                    ctx,
                    messages=state.messages,
                    terminal=True,
                    terminal_reason="direct_answer",
                )
                agent_run_repository.finish_step(
                    self._session,
                    step,
                    {"turn": turns, "direct_answer": True},
                    token_usage=decision_result.usage,
                )
                self._session.commit()
                return ResearchAgentRunOutcome(
                    None,
                    "direct_answer",
                    turns,
                    snapshot=self._build_snapshot(ctx),
                    evidences=self._react_evidences(ctx),
                    direct_answer=answer,
                )

            try:
                if decision_result.decision is None:
                    raise ValueError("RESEARCH_AGENT_DECISION_MISSING")
                ctx.apply_research_turn_decision(
                    decision_result.decision,
                    visible_tools=visible_tools,
                )
            except (TypeError, ValueError) as exc:
                # Finding/Todo 是服务端事实，非法增量不能部分写入；给模型一次
                # 可修正反馈，后续仍由停滞阈值负责收口。
                stall_turns += 1
                self._append_model_feedback(
                    state,
                    f"ResearchState 变更被拒绝：{str(exc)[:500]}。请修正后重试。",
                )
                agent_run_repository.finish_step(
                    self._session,
                    step,
                    {"turn": turns, "state_change_error": str(exc)[:500]},
                )
                self._persist_state(ctx, messages=state.messages)
                if stall_turns >= self._config.research_max_stall_turns:
                    return self._finalize_stalled(ctx, turns, state)
                continue

            actions = self._action_pairs(decision_result, turns)
            try:
                results = self._execute_actions(
                    ctx,
                    step,
                    actions,
                    visible_tools=visible_tools,
                )
            except AgentCancellationRequested:
                agent_run_repository.cancel_step(
                    self._session,
                    step,
                    "用户在 Research Agent 工具执行期间请求取消",
                )
                self._session.commit()
                return self._finalize_cancelled(
                    ctx,
                    turns,
                    "during_tool_call",
                    state,
                )
            except ResearchRuntimeInterrupted:
                raise
            except Exception as exc:  # noqa: BLE001 - 工具系统错误显式收口
                logger.warning("Research Agent 工具执行失败", exc_info=True)
                agent_run_repository.fail_step(self._session, step, str(exc)[:2_000])
                self._session.commit()
                return self._finalize_failed(ctx, turns, state, str(exc))

            for result in results:
                state.messages.append(self._tool_message(result))
            self._persist_state(ctx, messages=state.messages)
            agent_run_repository.finish_step(
                self._session,
                step,
                {
                    "turn": turns,
                    "tool_results": [
                        {
                            "tool_call_id": item.tool_call_id,
                            "tool_name": item.name.value,
                            "status": item.status.value,
                        }
                        for item in results
                    ],
                },
                token_usage=decision_result.usage,
            )
            self._session.commit()

            if ctx.react_completion() is not None:
                return self._finalize_completed(ctx, turns, state)
            if any(
                item.status is ToolResultStatus.WAITING_FOR_USER for item in results
            ):
                return ResearchAgentRunOutcome(
                    None,
                    "waiting_for_user",
                    turns,
                    snapshot=self._build_snapshot(ctx),
                )

            current_signature = self._progress_signature(ctx)
            failure_signature = self._failure_signature(ctx, results)
            if (
                failure_signature is not None
                and failure_signature == previous_failure_signature
            ):
                # 相同参数产生相同错误时，继续重试不会带来新信息；把它计入
                # 停滞轮数，避免可重试标记掩盖模型的重复错误。
                stall_turns += 1
            elif current_signature != previous_signature:
                stall_turns = 0
            elif self._has_actionable_failure(results):
                # 参数可修正的首次失败允许下一轮换参重试。
                stall_turns = 0
            else:
                stall_turns += 1
            previous_failure_signature = failure_signature
            previous_signature = current_signature
            if stall_turns >= self._config.research_max_stall_turns:
                return self._finalize_stalled(ctx, turns, state)

            if time.monotonic() - started_at >= ctx.budget.max_duration_seconds:
                return self._finalize_budget(ctx, turns, state, "duration_budget")

    def resume(self) -> ResearchAgentRunOutcome:
        """从 Run 的 derived_state 恢复 ResearchState 后继续执行。"""

        ctx, recovery = self._restore_context()
        outcome = self.run(ctx=ctx)
        return replace(outcome, recovery=recovery)

    # ------------------------------------------------------------------ #
    # 初始化与恢复
    # ------------------------------------------------------------------ #

    def _initial_context(
        self, requirement: ResearchAgentRequirement
    ) -> ResearchToolContext:
        raw_input = self._context_state_overlay.get("research_agent_input")
        agent_input = (
            ResearchAgentInput.model_validate(raw_input)
            if isinstance(raw_input, Mapping)
            else self._build_agent_input(requirement)
        )
        agent_context = AgentToolContext(
            session=self._session,
            oid=int(self._run_row.oid),
            user_id=getattr(self._run_row, "created_by", None),
            datasource_id=getattr(self._record, "datasource", None),
            execution_id=(
                f"agent:{self._run_row.id}"
                if getattr(self._run_row, "id", None) is not None
                else None
            ),
            chat_id=getattr(self._run_row, "chat_id", None),
            record_id=getattr(self._run_row, "record_id", None),
            dataset_id=getattr(self._record, "dataset_id", None),
            result_store=self._result_store,
            permission_version=self._context_state_overlay.get("permission_version"),
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
            semantic_retrieval_service=self._semantic_retrieval_service,
            compute_engine=self._compute_engine,
            agent_input=agent_input,
            budget=requirement.budget,
            cancellation=self._cancellation,
            trace_recorder=self._observation_recorder,
            failure_injector=self._failure_injector,
        )
        # Research 主路径只建立 ReAct 状态。
        ctx.bind_to_context(include_legacy_plan_state=False)
        return ctx

    def _restore_context(self) -> tuple[ResearchToolContext, ResearchRecoveryReport]:
        ctx, recovery = recover_research_agent_run(
            self._session,
            self._run_row,
            semantic_runtime=self._semantic_runtime,
            semantic_retrieval_service=self._semantic_retrieval_service,
            compute_engine=self._compute_engine,
            result_store=self._result_store,
            cancellation=self._cancellation,
            trace_recorder=self._observation_recorder,
            context_state_overlay=self._context_state_overlay,
        )
        derived = dict(getattr(self._run_row, "derived_state", None) or {})
        raw_messages = derived.get(_MESSAGES_KEY)
        messages = (
            restore_messages(raw_messages) if isinstance(raw_messages, list) else []
        )
        self._restored_messages = messages
        ctx.failure_injector = self._failure_injector
        return ctx, recovery

    def _build_agent_input(
        self, requirement: ResearchAgentRequirement
    ) -> ResearchAgentInput:
        execution = self._context_state_overlay.get("execution_requirement")
        assets: Mapping[str, Any] = {}
        if isinstance(execution, Mapping):
            snapshot = execution.get("asset_snapshot")
            if isinstance(snapshot, Mapping):
                raw_assets = snapshot.get("research_assets") or {}
                if isinstance(raw_assets, Mapping):
                    assets = raw_assets
        metrics: list[SemanticMetric] = []
        dimensions: list[SemanticDimension] = []
        metric_refs = tuple(
            dict.fromkeys(
                (
                    *requirement.scope.target_metric_refs,
                    *requirement.scope.driver_metric_refs,
                )
            )
        )
        for ref in metric_refs:
            item = assets.get(ref) if isinstance(assets, Mapping) else None
            payload = item if isinstance(item, Mapping) else {}
            metrics.append(
                SemanticMetric(
                    ref=ref,
                    name=str(
                        payload.get("display_name") or payload.get("biz_name") or ref
                    ),
                    description=str(payload.get("description") or ""),
                    aggregation="SUM",
                    dimensions=tuple(requirement.scope.dimension_refs),
                )
            )
        for ref in requirement.scope.dimension_refs:
            item = assets.get(ref) if isinstance(assets, Mapping) else None
            payload = item if isinstance(item, Mapping) else {}
            dimensions.append(
                SemanticDimension(
                    ref=ref,
                    name=str(
                        payload.get("display_name") or payload.get("biz_name") or ref
                    ),
                    description=str(payload.get("description") or ""),
                )
            )
        return ResearchAgentInput(
            agent_input_ref="agent_input_01",
            user_question=str(
                getattr(self._record, "question", None) or requirement.goal
            ),
            semantic_context=SemanticContext(
                metrics=tuple(metrics),
                dimensions=tuple(dimensions),
            ),
        )

    # ------------------------------------------------------------------ #
    # 单轮控制
    # ------------------------------------------------------------------ #

    def _build_runtime_state(self, ctx: ResearchToolContext) -> AgentRuntimeState:
        messages = getattr(self, "_restored_messages", None)
        if not isinstance(messages, list) or not messages:
            messages = [AgentMessage.user(ctx.current_agent_input().user_question)]
        state = AgentRuntimeState(
            run=self._run_row,
            record=self._record,
            context=ctx.context,
            messages=list(messages),
            budget=BudgetGuard(
                max_steps=self._config.max_steps,
                token_budget=self._config.token_budget,
                repeat_fuse_threshold=self._config.repeat_fuse_threshold,
                timeout_seconds=self._config.timeout_seconds,
            ),
            cancellation=self._cancellation or NeverCancelled(),
            system=AgentMessage.system(build_research_system_context(ctx.requirement)),
            runtime_context=AgentMessage.user(
                build_research_system_context(ctx.requirement)
            ),
        )
        self._restored_messages = None
        return state

    def _profile(
        self,
        ctx: ResearchToolContext,
        visible_tools: Sequence[str],
    ) -> ReasoningProfile:
        return replace(
            RESEARCH_REACT_PROFILE,
            fixed_tool_allowlist=tuple(visible_tools),
            working_state_builder=lambda _state: self._working_state(ctx),
        )

    def _working_state(self, ctx: ResearchToolContext) -> dict[str, Any]:
        evidence_items = []
        for evidence_id in ctx.known_evidence_ids():
            evidence = ctx.research_evidence(evidence_id)
            if evidence is not None:
                evidence_items.append(evidence)
        return project_research_react_state(
            ctx.current_agent_input(),
            ctx.research_state(),
            evidence=tuple(evidence_items),
            remaining_budget=self._remaining_budget(ctx),
            requirement=ctx.requirement,
        )

    def _visible_tools(
        self, ctx: ResearchToolContext, started_at: float
    ) -> tuple[str, ...]:
        usage = ctx.react_budget_usage()
        if usage.model_turns >= ctx.budget.max_model_calls - 1:
            # 最后一轮模型调用只用于提交 complete、partial 或
            # unanswerable，避免模型预算耗尽时没有结束判断机会。
            return (ResearchActionType.FINISH_RESEARCH.value,)
        data_budget_available = usage.query_calls < ctx.budget.max_queries
        compute_budget_available = usage.compute_calls < ctx.budget.max_queries
        search_budget_available = usage.semantic_search_calls < ctx.budget.max_queries
        if not data_budget_available:
            return (ResearchActionType.FINISH_RESEARCH.value,)
        visible = [
            ResearchActionType.FINISH_RESEARCH.value,
        ]
        if data_budget_available:
            visible.insert(0, ResearchActionType.QUERY_SEMANTIC_DATA.value)
        if compute_budget_available and bool(ctx.known_evidence_ids()):
            visible.insert(
                1 if data_budget_available else 0,
                ResearchActionType.COMPUTE_EVIDENCE.value,
            )
        if any(
            ctx.research_evidence_result_id(evidence_id) is not None
            for evidence_id in ctx.known_evidence_ids()
        ):
            visible.insert(
                2 if data_budget_available else 1,
                ResearchActionType.READ_EVIDENCE_ROWS.value,
            )
        if search_budget_available and self._semantic_context_has_gap(ctx):
            visible.insert(
                3 if data_budget_available else 1,
                ResearchActionType.SEARCH_SEMANTIC_ASSETS.value,
            )
        if ctx.current_agent_input().semantic_context.ambiguities:
            visible.append(ResearchActionType.REQUEST_CLARIFICATION.value)
        if time.monotonic() - started_at >= ctx.budget.max_duration_seconds:
            return (ResearchActionType.FINISH_RESEARCH.value,)
        # 澄清请求和结束判断是控制动作；若有待回答澄清，不再产生新动作。
        if ctx.clarification_request() is not None:
            return (ResearchActionType.FINISH_RESEARCH.value,)
        return tuple(dict.fromkeys(visible))

    @staticmethod
    def _semantic_context_has_gap(ctx: ResearchToolContext) -> bool:
        semantic_context = ctx.current_agent_input().semantic_context
        available = {item.ref for item in semantic_context.metrics} | {
            item.ref for item in semantic_context.dimensions
        }
        required = set(ctx.requirement.scope.target_metric_refs)
        required.update(ctx.requirement.scope.driver_metric_refs)
        required.update(ctx.requirement.scope.dimension_refs)
        return not required <= available

    def _remaining_budget(self, ctx: ResearchToolContext) -> RemainingBudget:
        usage = ctx.react_budget_usage()
        # ResearchBudget 当前没有独立 compute/search 轴，沿用 max_queries 作为
        # 每个动作轴的上限，避免把未声明的资源无限暴露给模型。
        elapsed_ms = (
            int(max(0.0, time.monotonic() - self._started_at) * 1000)
            if hasattr(self, "_started_at")
            else 0
        )
        return RemainingBudget(
            model_turns=max(ctx.budget.max_model_calls - usage.model_turns, 0),
            query_calls=max(ctx.budget.max_queries - usage.query_calls, 0),
            compute_calls=max(ctx.budget.max_queries - usage.compute_calls, 0),
            semantic_search_calls=max(
                ctx.budget.max_queries - usage.semantic_search_calls,
                0,
            ),
            wall_time_ms=max(ctx.budget.max_duration_seconds * 1000 - elapsed_ms, 0),
            query_cost=0.0,
        )

    def _action_pairs(
        self,
        decision: Any,
        turn: int,
    ) -> tuple[tuple[str, ResearchAction], ...]:
        calls = list(decision.response.tool_calls)
        pairs: list[tuple[str, ResearchAction]] = []
        for index, action in enumerate(decision.decision.actions):
            call_id = (
                calls[index].call_id
                if index < len(calls) and calls[index].call_id
                else f"turn-{turn}-action-{index + 1}"
            )
            pairs.append((call_id, action))
        return tuple(pairs)

    def _execute_actions(
        self,
        ctx: ResearchToolContext,
        step: Any,
        actions: Sequence[tuple[str, ResearchAction]],
        *,
        visible_tools: Sequence[str],
    ) -> tuple[ToolResult[Any], ...]:
        if not actions:
            raise ValueError("RESEARCH_AGENT_ACTIONS_EMPTY")
        run_id = getattr(self._run_row, "id", None)
        if run_id is None or getattr(step, "id", None) is None:
            raise ValueError("RESEARCH_AGENT_TOOL_CALL_OWNERSHIP_MISSING")
        budget_before = ctx.react_budget_usage()
        tool_runtime = ResearchToolRuntime(
            self._registry,
            max_workers=int(getattr(self._config, "tool_parallel_workers", 4) or 4),
            persist_result=ctx.record_research_tool_result,
            version_snapshot=ctx.requirement.version_snapshot,
        )
        rows = []
        research_state = ctx.context.state.get(RESEARCH_STATE_KEY)
        if not isinstance(research_state, dict):
            raise ValueError("RESEARCH_AGENT_STATE_INVALID")
        attempt_iterations = research_state.setdefault("attempt_iterations", {})
        if not isinstance(attempt_iterations, dict):
            raise ValueError("RESEARCH_AGENT_ATTEMPT_ITERATIONS_INVALID")
        # AttemptSummary 只保存动作事实；轮次单独持久化，供恢复、评测和
        # 并行方向判定使用，不把运行时字段混入跨边界 DTO。
        step_index = getattr(step, "step_index", None)
        iteration = int(step_index) if isinstance(step_index, int) else ctx.iteration
        for call_id, action in actions:
            args_summary = action.arguments.model_dump(mode="json")
            args_summary["_action_fingerprint"] = tool_runtime.action_fingerprint(
                action
            )
            rows.append(
                agent_run_repository.start_tool_call(
                    self._session,
                    run_id=run_id,
                    step_id=step.id,
                    tool_call_id=call_id,
                    tool_name=action.action_type.value,
                    args_summary=args_summary,
                )
            )
            attempt_iterations[f"attempt:{call_id}"] = iteration
        # 先提交 RUNNING 行，进程中断时恢复逻辑可以识别未完成动作。
        self._session.commit()
        results = self._execute_tool_batch_with_cancellation(
            tool_runtime,
            actions,
            ctx=ctx,
            visible_tools=visible_tools,
        )
        if self._is_cancelled(ctx):
            raise AgentCancellationRequested(CancellationStage.DURING_TOOL)
        for row, result in zip(rows, results, strict=True):
            agent_run_repository.finish_research_tool_call(
                self._session,
                row,
                result=result,
            )
        self._record_research_action_trace(
            ctx,
            step=step,
            actions=actions,
            results=results,
            visible_tools=visible_tools,
            budget_before=budget_before,
            budget_after=ctx.react_budget_usage(),
        )
        return results

    def _execute_tool_batch_with_cancellation(
        self,
        tool_runtime: ResearchToolRuntime,
        actions: Sequence[tuple[str, ResearchAction]],
        *,
        ctx: ResearchToolContext,
        visible_tools: Sequence[str],
    ) -> tuple[ToolResult[Any], ...]:
        """异步等待工具批次，并在取消时丢弃尚未收口的结果。"""

        result_queue: Queue[
            tuple[tuple[ToolResult[Any], ...] | None, Exception | None]
        ] = Queue(maxsize=1)
        detached = Event()

        def execute() -> None:
            try:
                result = tool_runtime.execute_batch(
                    actions,
                    context=ctx,
                    visible_tools=visible_tools,
                    run_status=ctx.current_status,
                    remaining_budget=self._remaining_budget(ctx),
                    cancelled=self._cancellation or ctx.cancellation,
                )
            except Exception as exc:  # noqa: BLE001 - 主线程负责统一收口
                if detached.is_set():
                    logger.warning("工具批次在取消后返回异常，结果已丢弃", exc_info=True)
                result_queue.put((None, exc))
                return
            result_queue.put((result, None))

        Thread(
            target=execute,
            name=f"research-tool-batch-{self._run_row.id}",
            daemon=True,
        ).start()

        while True:
            try:
                results, error = result_queue.get(timeout=0.05)
            except Empty:
                if self._is_cancelled(ctx):
                    detached.set()
                    ctx.discard_pending_tool_results()
                    raise AgentCancellationRequested(CancellationStage.DURING_TOOL)
                continue
            if self._is_cancelled(ctx):
                detached.set()
                ctx.discard_pending_tool_results()
                raise AgentCancellationRequested(CancellationStage.DURING_TOOL)
            if error is not None:
                raise error
            if results is None:
                raise RuntimeError("RESEARCH_TOOL_BATCH_RESULT_MISSING")
            return results

    def _record_research_action_trace(
        self,
        ctx: ResearchToolContext,
        *,
        step: Any,
        actions: Sequence[tuple[str, ResearchAction]],
        results: Sequence[ToolResult[Any]],
        visible_tools: Sequence[str],
        budget_before: Any,
        budget_after: Any,
    ) -> None:
        """记录动作批次及每个动作的 ToolResult 和 Evidence 依赖。"""

        run_id = getattr(self._run_row, "id", None)
        step_id = getattr(step, "id", None)
        if not isinstance(run_id, int) or run_id <= 0 or not isinstance(step_id, int):
            return
        fingerprints = [
            self._research_action_fingerprint(ctx, call_id, action)
            for call_id, action in actions
        ]
        iteration = getattr(step, "step_index", None)
        iteration = iteration if isinstance(iteration, int) else ctx.iteration
        prompt_version = ctx.context.state.get("research_prompt_version")
        with self._recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"research_action_batch:{iteration}",
                node_type=TraceNodeType.PHASE,
                name="research_action_batch",
                display_name="Research 动作批次",
                metadata={
                    "step_id": step_id,
                    "iteration": iteration,
                    "prompt_version": prompt_version,
                },
            ),
            input_data={
                "step_id": step_id,
                "iteration": iteration,
                "visible_tools": list(visible_tools),
                "actions": [
                    {
                        "tool_call_id": call_id,
                        "tool_name": action.action_type.value,
                        "action_fingerprint": fingerprint,
                    }
                    for (call_id, action), fingerprint in zip(
                        actions, fingerprints, strict=True
                    )
                ],
            },
        ) as batch_node:
            batch_node.set_output(
                {
                    "action_count": len(actions),
                    "result_count": len(results),
                    "statuses": [item.status.value for item in results],
                    "visible_tools": list(visible_tools),
                }
            )
            batch_node.set_state_diff(
                {"budget_usage": budget_before.model_dump(mode="json")},
                {"budget_usage": budget_after.model_dump(mode="json")},
            )
            batch_node.set_output_detail(
                {
                    "actions": [
                        {
                            "tool_call_id": call_id,
                            "tool_name": action.action_type.value,
                            "purpose": action.purpose,
                            "arguments": action.arguments.model_dump(mode="json"),
                            "action_fingerprint": fingerprint,
                        }
                        for (call_id, action), fingerprint in zip(
                            actions, fingerprints, strict=True
                        )
                    ],
                    "tool_results": [
                        item.model_dump(mode="json") for item in results
                    ],
                    "budget_before": budget_before.model_dump(mode="json"),
                    "budget_after": budget_after.model_dump(mode="json"),
                }
            )
            for (call_id, action), result, fingerprint in zip(
                actions, results, fingerprints, strict=True
            ):
                self._record_research_tool_trace(
                    run_id,
                    step_id,
                    iteration,
                    prompt_version,
                    call_id,
                    action,
                    result,
                    fingerprint,
                    budget_after,
                )

    @staticmethod
    def _research_action_fingerprint(
        ctx: ResearchToolContext,
        call_id: str,
        action: ResearchAction,
    ) -> str:
        """优先读取已持久化 Attempt 中的最终指纹。"""

        attempt_id = f"attempt:{call_id}"
        for attempt in ctx.react_attempts():
            if attempt.attempt_id == attempt_id:
                return attempt.action_fingerprint
        return research_action_fingerprint(
            action,
            version_snapshot=ctx.requirement.version_snapshot,
        )

    def _record_research_tool_trace(
        self,
        run_id: int,
        step_id: int,
        iteration: int,
        prompt_version: Any,
        call_id: str,
        action: ResearchAction,
        result: ToolResult[Any],
        fingerprint: str,
        budget_after: Any,
    ) -> None:
        """记录单个 Research 工具结果，包含 Evidence 依赖摘要。"""

        result_payload = result.model_dump(mode="json")
        result_data = result_payload.get("result")
        evidence_id = (
            result_data.get("evidence_id")
            if isinstance(result_data, dict)
            else None
        )
        parent_evidence_ids = (
            result_data.get("parent_evidence_ids", [])
            if isinstance(result_data, dict)
            else []
        )
        rejected = (
            isinstance(result_data, dict)
            and result_data.get("decision") == "rejected"
        )
        status = (
            TraceNodeStatus.REJECTED
            if rejected
            else {
                ToolResultStatus.SUCCEEDED: TraceNodeStatus.SUCCEEDED,
                ToolResultStatus.FAILED: TraceNodeStatus.FAILED,
                ToolResultStatus.WAITING_FOR_USER: TraceNodeStatus.WAITING,
            }[result.status]
        )
        with self._recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"research_tool_result:{iteration}:{call_id}",
                node_type=TraceNodeType.TOOL,
                name="research_tool_result",
                display_name=f"Research 工具结果：{action.action_type.value}",
                attributes=tool_attributes(
                    tool_name=action.action_type.value,
                    run_id=run_id,
                    step_id=step_id,
                    tool_call_id=call_id,
                ),
                metadata={
                    "iteration": iteration,
                    "prompt_version": prompt_version,
                    "action_fingerprint": fingerprint,
                },
            ),
            input_data={
                "tool_call_id": call_id,
                "tool_name": action.action_type.value,
                "action_fingerprint": fingerprint,
                "purpose": action.purpose,
                "arguments": action.arguments.model_dump(mode="json"),
            },
        ) as result_node:
            result_node.set_status(status)
            result_node.set_attribute("gen_ai.tool.call.result", result.status.value)
            if result.error is not None:
                result_node.set_attribute("gen_ai.tool.error.type", result.error.code)
            result_node.set_output(
                {
                    "tool_call_id": call_id,
                    "tool_name": action.action_type.value,
                    "status": result.status.value,
                    "action_fingerprint": fingerprint,
                    "evidence_id": evidence_id,
                    "parent_evidence_ids": parent_evidence_ids,
                    "completion_decision": (
                        result_data.get("decision")
                        if isinstance(result_data, dict)
                        else None
                    ),
                    "error_code": result.error.code if result.error else None,
                    "budget_usage_after": budget_after.model_dump(mode="json"),
                }
            )
            result_node.set_output_detail(
                {
                    "tool_result": result_payload,
                    "evidence_dependencies": {
                        "evidence_id": evidence_id,
                        "parent_evidence_ids": parent_evidence_ids,
                    },
                }
            )

    # ------------------------------------------------------------------ #
    # 终态与持久化
    # ------------------------------------------------------------------ #

    def _finalize_completed(
        self,
        ctx: ResearchToolContext,
        turns: int,
        state: AgentRuntimeState,
    ) -> ResearchAgentRunOutcome:
        self._persist_state(
            ctx,
            messages=state.messages,
            terminal=True,
            terminal_reason="finish_research_accepted",
        )
        return ResearchAgentRunOutcome(
            ctx.react_completion(),
            "finished",
            turns,
            snapshot=self._build_snapshot(ctx),
            evidences=self._react_evidences(ctx),
        )

    def _finalize_budget(
        self,
        ctx: ResearchToolContext,
        turns: int,
        state: AgentRuntimeState,
        reason: str,
    ) -> ResearchAgentRunOutcome:
        completion = self._business_completion(
            ctx,
            "partial" if ctx.known_evidence_ids() else "unanswerable",
            "budget_exhausted",
            f"研究因预算不足结束，已保留 {len(ctx.known_evidence_ids())} 条 Evidence。",
        )
        self._persist_state(
            ctx,
            messages=state.messages,
            terminal=True,
            terminal_reason=reason,
        )
        return ResearchAgentRunOutcome(
            completion,
            "budget_exhausted",
            turns,
            snapshot=self._build_snapshot(ctx),
            evidences=self._react_evidences(ctx),
        )

    def _finalize_stalled(
        self,
        ctx: ResearchToolContext,
        turns: int,
        state: AgentRuntimeState,
    ) -> ResearchAgentRunOutcome:
        empty_result_observed = (
            not ctx.known_evidence_ids()
            and any(
                attempt.error is not None
                and attempt.error.code == ToolErrorCode.EMPTY_RESULT.value
                for attempt in ctx.react_attempts()
            )
        )
        stop_reason = "data_insufficient" if empty_result_observed else "stalled"
        completion_code = (
            "data_insufficient" if empty_result_observed else "stalled"
        )
        summary = (
            "查询确认当前筛选条件下没有可用数据，研究无法继续。"
            if empty_result_observed
            else "连续多轮没有产生新的研究状态变化，研究已停止。"
        )
        completion = self._business_completion(
            ctx,
            "partial" if ctx.known_evidence_ids() else "unanswerable",
            completion_code,
            summary,
        )
        self._persist_state(
            ctx,
            messages=state.messages,
            terminal=True,
            terminal_reason=completion_code,
        )
        return ResearchAgentRunOutcome(
            completion,
            stop_reason,
            turns,
            snapshot=self._build_snapshot(ctx),
            evidences=self._react_evidences(ctx),
        )

    def _finalize_cancelled(
        self,
        ctx: ResearchToolContext,
        turns: int,
        stage: str,
        state: AgentRuntimeState,
    ) -> ResearchAgentRunOutcome:
        self._close_running_tool_calls(
            status=AgentToolCallStatus.INTERRUPTED,
            error_code="tool_call_interrupted",
        )
        ctx.set_current_status(ResearchStateStatus.CANCELLED)
        completion = ResearchRuntimeTermination(
            status="cancelled",
            reason="cancelled",
            summary=f"研究在 {stage} 阶段被取消，已完成的 Evidence 已保留。",
            evidence_ids=tuple(ctx.known_evidence_ids())[:_MAX_COMPLETION_EVIDENCE_IDS],
        )
        self._persist_state(
            ctx,
            messages=state.messages,
            terminal=True,
            terminal_reason=stage,
        )
        return ResearchAgentRunOutcome(
            completion,
            "cancelled",
            turns,
            snapshot=self._build_snapshot(ctx),
            evidences=self._react_evidences(ctx),
        )

    def _finalize_failed(
        self,
        ctx: ResearchToolContext,
        turns: int,
        state: AgentRuntimeState,
        message: str,
    ) -> ResearchAgentRunOutcome:
        self._close_running_tool_calls(
            status=AgentToolCallStatus.FAILED,
            error_code="tool_undeclared_exception",
        )
        ctx.set_current_status(ResearchStateStatus.FAILED)
        completion = ResearchRuntimeTermination(
            status="failed",
            reason="execution_failed",
            summary=message[:4_000] or "Research Runtime 发生不可恢复错误。",
            evidence_ids=tuple(ctx.known_evidence_ids())[:_MAX_COMPLETION_EVIDENCE_IDS],
        )
        self._persist_state(
            ctx,
            messages=state.messages,
            terminal=True,
            terminal_reason="execution_failed",
        )
        return ResearchAgentRunOutcome(
            completion,
            "failed",
            turns,
            snapshot=self._build_snapshot(ctx),
            evidences=self._react_evidences(ctx),
        )

    def _already_finished(self, ctx: ResearchToolContext) -> ResearchAgentRunOutcome:
        return ResearchAgentRunOutcome(
            ctx.react_completion(),
            "already_finished",
            0,
            snapshot=self._build_snapshot(ctx),
            evidences=self._react_evidences(ctx),
        )

    def _business_completion(
        self,
        ctx: ResearchToolContext,
        status: str,
        code: str,
        summary: str,
    ) -> Completion:
        evidence_ids = tuple(ctx.known_evidence_ids())[:_MAX_COMPLETION_EVIDENCE_IDS]
        finding_ids = tuple(
            item.finding_id
            for item in ctx.react_findings()
            if item.status == "confirmed"
        )
        completion = Completion(
            status=cast(Any, status),
            summary=summary,
            finding_ids=finding_ids,
            evidence_ids=evidence_ids,
            limitations=(
                CompletionLimitation(
                    code=code,
                    description=summary,
                    impact="当前结果可能未覆盖用户问题的全部分析范围。",
                ),
            ),
        )
        ctx.save_react_completion(completion)
        return completion

    def _build_snapshot(self, ctx: ResearchToolContext) -> ResearchStateSnapshot:
        return build_research_state_snapshot(ctx)

    @staticmethod
    def _react_evidences(ctx: ResearchToolContext) -> tuple[Evidence, ...]:
        """把当前 Run 的完整 ReAct Evidence 交给最终回答边界。"""

        return tuple(
            evidence
            for evidence_id in ctx.known_evidence_ids()
            if (evidence := ctx.research_evidence(evidence_id)) is not None
        )

    def _persist_state(
        self,
        ctx: ResearchToolContext,
        *,
        messages: Sequence[AgentMessage],
        terminal: bool = False,
        terminal_reason: str | None = None,
    ) -> None:
        derived = dict(getattr(self._run_row, "derived_state", None) or {})
        research_state = ctx.context.state.get(RESEARCH_STATE_KEY)
        if isinstance(research_state, dict):
            derived[RESEARCH_STATE_KEY] = research_state
        result_sets = ctx.context.state.get(_RESULT_SETS_KEY)
        if isinstance(result_sets, dict):
            derived[_RESULT_SETS_KEY] = {
                **dict(derived.get(_RESULT_SETS_KEY) or {}),
                **result_sets,
            }
        analysis = ctx.context.state.get(ANALYSIS_EVIDENCE_REGISTRY_KEY)
        if isinstance(analysis, dict):
            derived[ANALYSIS_EVIDENCE_REGISTRY_KEY] = dict(analysis)
        prompt_version = ctx.context.state.get("research_prompt_version")
        if isinstance(prompt_version, str) and prompt_version:
            derived["research_prompt_version"] = prompt_version
        if messages:
            derived[_MESSAGES_KEY] = [item.model_dump(mode="json") for item in messages]
        snapshot = self._build_snapshot(ctx)
        derived[_SNAPSHOT_KEY] = snapshot.model_dump(mode="json")
        agent_run_repository.update_run(
            self._session,
            self._run_row,
            derived_state=derived,
            messages=[item.model_dump(mode="json") for item in messages],
        )
        if terminal:
            self._record_research_terminal_trace(
                ctx,
                reason=terminal_reason or ctx.current_status.value,
            )
            self._inject_failure("before_terminal_commit")
        self._session.commit()
        self._inject_failure("after_state_save")

    def _record_research_terminal_trace(
        self,
        ctx: ResearchToolContext,
        *,
        reason: str,
    ) -> None:
        """记录 Research Run 的终态和结束原因。"""

        run_id = getattr(self._run_row, "id", None)
        if not isinstance(run_id, int) or run_id <= 0:
            return
        state_status = ctx.current_status
        trace_status = {
            ResearchStateStatus.COMPLETED: TraceNodeStatus.SUCCEEDED,
            ResearchStateStatus.PARTIAL: TraceNodeStatus.PARTIAL,
            ResearchStateStatus.UNANSWERABLE: TraceNodeStatus.REJECTED,
            ResearchStateStatus.CANCELLED: TraceNodeStatus.CANCELLED,
            ResearchStateStatus.FAILED: TraceNodeStatus.FAILED,
            ResearchStateStatus.WAITING_FOR_USER: TraceNodeStatus.WAITING,
            ResearchStateStatus.RUNNING: TraceNodeStatus.FAILED,
        }[state_status]
        prompt_version = ctx.context.state.get("research_prompt_version")
        completion = ctx.react_completion()
        with self._recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"research_terminal:{reason}",
                node_type=TraceNodeType.VALIDATION,
                name="research_terminal",
                display_name="Research 终态",
                metadata={
                    "status": state_status.value,
                    "reason": reason,
                    "prompt_version": prompt_version,
                },
            ),
            input_data={
                "status": state_status.value,
                "reason": reason,
                "prompt_version": prompt_version,
            },
        ) as terminal_node:
            terminal_node.set_status(trace_status)
            terminal_node.set_output(
                {
                    "status": state_status.value,
                    "reason": reason,
                    "completion_status": completion.status if completion else None,
                    "evidence_count": len(ctx.known_evidence_ids()),
                    "budget_usage": ctx.react_budget_usage().model_dump(mode="json"),
                }
            )
            terminal_node.set_output_detail(
                {
                    "completion": (
                        completion.model_dump(mode="json") if completion else None
                    ),
                    "research_state": ctx.research_state().model_dump(mode="json"),
                }
            )

    def _close_running_tool_calls(
        self,
        *,
        status: AgentToolCallStatus,
        error_code: str,
    ) -> None:
        run_id = getattr(self._run_row, "id", None)
        if run_id is None:
            return
        for row in agent_run_repository.list_running_tool_calls(self._session, run_id):
            agent_run_repository.finish_tool_call(
                self._session,
                row,
                status=status,
                result_summary={
                    "success": False,
                    "status": status.value,
                    "error_code": error_code,
                },
                error_code=error_code,
            )

    # ------------------------------------------------------------------ #
    # 辅助判断
    # ------------------------------------------------------------------ #

    def _progress_signature(self, ctx: ResearchToolContext) -> tuple[Any, ...]:
        state = ctx.research_state()
        return (
            state.agent_input_ref,
            tuple(state.evidence_refs),
            tuple(item.model_dump(mode="json") for item in state.findings),
            tuple(item.model_dump(mode="json") for item in state.todo_items),
        )

    @staticmethod
    def _has_actionable_failure(results: Sequence[ToolResult[Any]]) -> bool:
        return any(
            item.status is ToolResultStatus.FAILED
            and item.error is not None
            and (item.error.retryable or item.error.parameter_retryable)
            for item in results
        )

    @staticmethod
    def _failure_signature(
        ctx: ResearchToolContext,
        results: Sequence[ToolResult[Any]],
    ) -> tuple[Any, ...] | None:
        """生成本轮失败签名，用于识别相同参数的连续错误。"""

        failures: list[tuple[Any, ...]] = []
        attempts = {item.attempt_id: item for item in ctx.react_attempts()}
        for result in results:
            if result.status is not ToolResultStatus.FAILED or result.error is None:
                continue
            attempt = attempts.get(f"attempt:{result.tool_call_id}")
            failures.append(
                (
                    result.name.value,
                    result.error.code,
                    result.error.message,
                    attempt.action_fingerprint if attempt is not None else None,
                )
            )
        return tuple(failures) if failures else None

    @staticmethod
    def _tool_message(result: ToolResult[Any]) -> AgentMessage:
        payload = result.model_dump(mode="json")
        content = orjson.dumps(payload).decode("utf-8")[:12_000]
        return AgentMessage.tool(content, result.tool_call_id)

    @staticmethod
    def _append_model_feedback(state: AgentRuntimeState, content: str) -> None:
        state.messages.append(
            AgentMessage.user(f"<system-reminder>{content}</system-reminder>")
        )

    def _is_cancelled(self, ctx: ResearchToolContext) -> bool:
        signal = self._cancellation or ctx.cancellation
        checker = getattr(signal, "is_cancelled", None)
        return bool(checker()) if callable(checker) else False

    def _inject_failure(self, point: str) -> None:
        """触发测试用故障点，正式运行没有注入器时保持无副作用。"""

        if self._failure_injector is not None:
            self._failure_injector(point)


__all__ = [
    "ResearchAgentRunOutcome",
    "ResearchAgentRuntime",
    "ResearchRuntimeInterrupted",
    "ResearchRuntimeTermination",
]
