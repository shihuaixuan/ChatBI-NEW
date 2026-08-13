"""AgentLoop：LLM 自主规划 + 受控工具循环。

LLM 拥有：选择工具、组织参数、决定顺序、决定何时澄清与结束。
LLM 没有：越出白名单、绕过守护、超出预算（BudgetGuard 硬/软上限）。
状态即消息历史：run.messages 持久化除 system 外的全部消息，恢复=反序列化继续。
工具运行时内核见 apps.tool；本模块只负责 ChatBI 编排策略。
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from typing import Any

from apps.chatbi.errors import QuestionUnderstandingError, SemanticClarificationError
from apps.chatbi.models import (
    AgentErrorClass,
    ChatbiAgentClarification,
    ChatbiAgentRun,
)
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.messages import close_unfinished_tool_calls
from apps.chatbi.orchestration.agent.preparation import AgentInputPreparer
from apps.chatbi.orchestration.agent.reasoning import AgentReasoner
from apps.chatbi.orchestration.agent.state import (
    AgentRuntimeState,
    AgentRuntimeStateFactory,
)
from apps.chatbi.orchestration.agent.tool_execution import AgentToolExecutor
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.event import EventPublisher, RenderEvent
from apps.trace import (
    AgentTraceRecorder,
    TraceNodeHandle,
    TraceNodeSpec,
    TraceNodeStatus,
    TraceNodeType,
    agent_attributes,
)

__all__ = ["AgentLoop"]


class AgentLoop:
    def __init__(
        self,
        session: Any,
        *,
        event_publisher: EventPublisher,
        recorder: AgentTraceRecorder,
        lifecycle: AgentLifecycle,
        reasoner: AgentReasoner,
        tool_executor: AgentToolExecutor,
        input_preparer: AgentInputPreparer,
        state_factory: AgentRuntimeStateFactory,
    ) -> None:
        self.session = session
        self.event_publisher = event_publisher
        self.recorder = recorder
        self.lifecycle = lifecycle
        self.reasoner = reasoner
        self.tool_executor = tool_executor
        self.input_preparer = input_preparer
        self.state_factory = state_factory

    # ---- 入口 ----

    def run(self, run: ChatbiAgentRun, record: Any) -> Iterator[RenderEvent]:
        """在完整生成器生命周期内记录一次 Agent 调用。"""

        terminal_status: TraceNodeStatus | None = None
        with self.recorder.node(
            TraceNodeSpec(
                run_id=run.id or 0,
                node_key="invocation:initial",
                node_type=TraceNodeType.INVOCATION,
                name="invoke_agent",
                display_name="首次执行",
                attributes=agent_attributes(
                    run_id=run.id or 0,
                    record_id=record.id or 0,
                    chat_id=run.chat_id,
                ),
            ),
            input_data={"record_id": record.id or 0, "chat_id": run.chat_id},
        ) as node:
            terminal_status = yield from _trace_terminal_result(
                self._run(run, record),
                node,
            )
        if (
            terminal_status is not None
            and terminal_status is not TraceNodeStatus.WAITING
        ):
            self.recorder.finish_run(
                run.id or 0,
                terminal_status,
                output_summary={"run_status": run.status},
                error_code=run.error_class,
                error_category="agent_run" if run.error else None,
                error=run.error,
            )

    def _run(self, run: ChatbiAgentRun, record: Any) -> Iterator[RenderEvent]:
        state = self.state_factory.create(run, record)
        yield from self.lifecycle.start(state)

        try:
            ready = yield from self.input_preparer.prepare_initial(state)
            if not ready:
                return
            yield from self._loop(state)
        except QuestionUnderstandingError as exc:
            yield from self.lifecycle.fail(
                state,
                str(exc),
                AgentErrorClass.UNDERSTANDING.value,
            )
        except Exception as exc:  # 任意未预期异常收敛为失败事件，避免 SSE 静默中断。
            message = str(exc) or exc.__class__.__name__
            yield from self.lifecycle.fail(state, message, AgentErrorClass.UNEXPECTED.value)

    def resume(
        self,
        run: ChatbiAgentRun,
        record: Any,
        clarification: ChatbiAgentClarification,
        answer_text: str,
    ) -> Iterator[RenderEvent]:
        """以新的调用 span 恢复挂起的 Agent run。"""

        clarification_id = getattr(clarification, "id", None)
        resume_key = clarification_id or clarification.tool_call_id or "pending"
        terminal_status: TraceNodeStatus | None = None
        with self.recorder.node(
            TraceNodeSpec(
                run_id=run.id or 0,
                node_key=f"invocation:resume:{resume_key}",
                node_type=TraceNodeType.INVOCATION,
                name="invoke_agent",
                display_name="澄清恢复",
                attributes=agent_attributes(
                    run_id=run.id or 0,
                    record_id=record.id or 0,
                    chat_id=run.chat_id,
                ),
                metadata={"clarification_id": clarification_id},
            ),
            input_data={"record_id": record.id or 0, "chat_id": run.chat_id},
        ) as node:
            terminal_status = yield from _trace_terminal_result(
                self._resume(run, record, clarification, answer_text),
                node,
            )
        if (
            terminal_status is not None
            and terminal_status is not TraceNodeStatus.WAITING
        ):
            self.recorder.finish_run(
                run.id or 0,
                terminal_status,
                output_summary={"run_status": run.status},
                error_code=run.error_class,
                error_category="agent_run" if run.error else None,
                error=run.error,
            )

    def _resume(
        self,
        run: ChatbiAgentRun,
        record: Any,
        clarification: ChatbiAgentClarification,
        answer_text: str,
    ) -> Iterator[RenderEvent]:
        """从澄清记录声明的恢复边界继续，不重跑已经完成的问题理解。"""

        state = self.state_factory.create(run, record)
        try:
            ready = yield from self.input_preparer.prepare_resume(
                state,
                clarification,
                answer_text,
            )
            if not ready:
                return
            yield from self._loop(state)
        except QuestionUnderstandingError as exc:
            yield from self.lifecycle.fail(
                state,
                str(exc),
                AgentErrorClass.UNDERSTANDING.value,
            )
        except SemanticClarificationError as exc:
            yield from self.lifecycle.fail(
                state,
                str(exc),
                AgentErrorClass.RETRIEVAL.value,
            )
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            yield from self.lifecycle.fail(state, message, AgentErrorClass.UNEXPECTED.value)

    # ---- 主循环 ----

    def _loop(self, state: AgentRuntimeState) -> Iterator[RenderEvent]:
        run = state.run
        record = state.record
        ctx = state.context
        messages = state.messages
        budget = state.budget

        while True:
            step_index = budget.steps + 1
            state_before = _trace_runtime_snapshot(state)
            with self.recorder.node(
                TraceNodeSpec(
                    run_id=state.require_run_id(),
                    node_key=f"react_iteration:{step_index}",
                    node_type=TraceNodeType.PHASE,
                    name="react_iteration",
                    display_name=f"ReAct 第 {step_index} 轮",
                    metadata={"step_index": step_index},
                ),
                input_data={
                    "step_index": step_index,
                    "budget_steps": budget.steps,
                    "tokens_used": budget.tokens_used,
                },
                input_detail={"runtime_state": state_before},
            ) as iteration_node:
                cancelled = state.cancellation.is_cancelled()
                mode = "cancelled" if cancelled else budget.planning_mode()
                guard_reason: str | None = None
                guard_error_class: str | None = None
                soft_tools_available = True
                with self.recorder.node(
                    TraceNodeSpec(
                        run_id=state.require_run_id(),
                        node_key=f"react_guard:{step_index}",
                        node_type=TraceNodeType.VALIDATION,
                        name="validate_react_iteration",
                        display_name="检查取消、预算与可用动作",
                        metadata={"step_index": step_index},
                    ),
                    input_data={"step_index": step_index},
                    input_detail={"budget": state.budget_snapshot()},
                ) as guard_node:
                    if cancelled:
                        guard_reason = "用户已请求取消运行"
                        guard_node.set_status(TraceNodeStatus.CANCELLED)
                    elif mode == "exhausted":
                        guard_reason = "运行预算已耗尽"
                        guard_error_class = AgentErrorClass.BUDGET.value
                        guard_node.set_status(TraceNodeStatus.REJECTED)
                    elif mode == "soft":
                        soft_tools_available = bool(
                            self.reasoner.available_tool_names(state, mode)
                        )
                        if not soft_tools_available:
                            guard_reason = "软预算模式下没有可用收口动作"
                            guard_error_class = AgentErrorClass.BUDGET.value
                            guard_node.set_status(TraceNodeStatus.REJECTED)
                    if guard_reason is None:
                        verdict = budget.check_before_step()
                        if not verdict.allowed:
                            guard_reason = verdict.reason
                            guard_error_class = verdict.error_class
                            guard_node.set_status(TraceNodeStatus.REJECTED)
                    guard_node.set_output(
                        {
                            "allowed": guard_reason is None,
                            "cancelled": cancelled,
                            "planning_mode": mode,
                            "soft_tools_available": soft_tools_available,
                            "reason": guard_reason,
                            "error_class": guard_error_class,
                        }
                    )

                if cancelled:
                    _set_iteration_result(
                        iteration_node,
                        state_before,
                        state,
                        "cancelled",
                        TraceNodeStatus.CANCELLED,
                    )
                    yield from self.lifecycle.cancel(state)
                    _set_iteration_result(
                        iteration_node,
                        state_before,
                        state,
                        "cancelled",
                        TraceNodeStatus.CANCELLED,
                    )
                    return
                if guard_reason is not None:
                    has_execution = isinstance(
                        ctx.state.get("last_execution"), dict
                    ) and bool((ctx.state.get("last_execution") or {}).get("sql"))
                    _set_iteration_result(
                        iteration_node,
                        state_before,
                        state,
                        "budget_finished" if has_execution else "budget_failed",
                        (
                            TraceNodeStatus.SUCCEEDED
                            if has_execution
                            else TraceNodeStatus.FAILED
                        ),
                        reason=guard_reason,
                    )
                    yield from self._budget_exhausted(
                        state,
                        reason=guard_reason,
                        error_class=guard_error_class,
                    )
                    _set_iteration_result(
                        iteration_node,
                        state_before,
                        state,
                        "budget_finished" if has_execution else "budget_failed",
                        (
                            TraceNodeStatus.SUCCEEDED
                            if has_execution
                            else TraceNodeStatus.FAILED
                        ),
                        reason=guard_reason,
                    )
                    return

                with self.recorder.node(
                    TraceNodeSpec(
                        run_id=state.require_run_id(),
                        node_key=f"persist_step_started:{step_index}",
                        node_type=TraceNodeType.PERSISTENCE,
                        name="persist_agent_step_started",
                        display_name="创建 Agent 步骤记录",
                        metadata={"step_index": step_index},
                    ),
                    input_data={"step_index": step_index, "planning_mode": mode},
                ) as step_node:
                    step = agent_run_repository.start_step(
                        self.session,
                        run,
                        step_index,
                    )
                    self.session.commit()
                    step_node.set_output({"step_id": step.id, "status": step.status})
                    yield self._emit(
                        state,
                        "step-started",
                        {
                            "record_id": record.id,
                            "step_id": step.id,
                            "step_index": step_index,
                        },
                        step.id,
                    )

                decision = self.reasoner.decide(
                    state,
                    mode,
                    step_id=step.id,
                    step_index=step_index,
                )
                usage = decision.usage
                text = decision.reasoning
                if text:
                    yield self._emit(
                        state,
                        "thinking",
                        {"record_id": record.id, "content": text},
                        step.id,
                    )

                cancelled_after_reasoning = state.cancellation.is_cancelled()
                with self.recorder.node(
                    TraceNodeSpec(
                        run_id=state.require_run_id(),
                        node_key=f"post_reasoning_guard:{step_index}",
                        node_type=TraceNodeType.VALIDATION,
                        name="validate_post_reasoning_state",
                        display_name="检查模型返回后的取消状态",
                        metadata={"step_id": step.id, "step_index": step_index},
                    ),
                    input_data={"cancelled": cancelled_after_reasoning},
                ) as post_guard_node:
                    post_guard_node.set_output(
                        {"allowed": not cancelled_after_reasoning}
                    )
                    if cancelled_after_reasoning:
                        post_guard_node.set_status(TraceNodeStatus.CANCELLED)

                if cancelled_after_reasoning:
                    agent_run_repository.fail_step(
                        self.session,
                        step,
                        "用户在模型规划期间请求取消",
                    )
                    _set_iteration_result(
                        iteration_node,
                        state_before,
                        state,
                        "cancelled_after_reasoning",
                        TraceNodeStatus.CANCELLED,
                        step_id=step.id,
                    )
                    yield from self.lifecycle.cancel(state)
                    _set_iteration_result(
                        iteration_node,
                        state_before,
                        state,
                        "cancelled_after_reasoning",
                        TraceNodeStatus.CANCELLED,
                        step_id=step.id,
                    )
                    return

                if decision.is_direct_answer:
                    direct_answer_allowed = _allows_direct_answer(state)
                    with self.recorder.node(
                        TraceNodeSpec(
                            run_id=state.require_run_id(),
                            node_key=f"direct_answer_guard:{step_index}",
                            node_type=TraceNodeType.VALIDATION,
                            name="validate_direct_answer",
                            display_name="校验直接回答条件",
                            metadata={"step_id": step.id},
                        ),
                        input_data={
                            "has_execution": bool(ctx.state.get("last_execution")),
                            "answer_length": len(text),
                        },
                    ) as answer_guard_node:
                        answer_guard_node.set_output(
                            {"allowed": direct_answer_allowed}
                        )
                        if not direct_answer_allowed:
                            answer_guard_node.set_status(TraceNodeStatus.REJECTED)
                    if not direct_answer_allowed:
                        close_unfinished_tool_calls(messages)
                        agent_run_repository.fail_step(
                            self.session,
                            step,
                            "问数消息没有成功查询结果，禁止直接回答",
                        )
                        _set_iteration_result(
                            iteration_node,
                            state_before,
                            state,
                            "direct_answer_rejected",
                            TraceNodeStatus.FAILED,
                            step_id=step.id,
                        )
                        yield from self.lifecycle.fail(
                            state,
                            "问数消息必须成功执行查询后才能结束，禁止生成看似来自数据库的直接回答。",
                            AgentErrorClass.SQL.value,
                        )
                        _set_iteration_result(
                            iteration_node,
                            state_before,
                            state,
                            "direct_answer_rejected",
                            TraceNodeStatus.FAILED,
                            step_id=step.id,
                        )
                        return
                    close_unfinished_tool_calls(messages)
                    agent_run_repository.finish_step(
                        self.session,
                        step,
                        {"mode": "direct_answer"},
                        usage,
                    )
                    _set_iteration_result(
                        iteration_node,
                        state_before,
                        state,
                        "direct_answer_finished",
                        TraceNodeStatus.SUCCEEDED,
                        step_id=step.id,
                    )
                    yield from self.lifecycle.finish(
                        state,
                        answer=text or "（模型未给出回答）",
                        chart={},
                        sql=(ctx.state.get("last_execution") or {}).get("sql"),
                        step_id=step.id,
                        full_data=ctx.state.get("full_data"),
                        execution=ctx.state.get("last_execution"),
                    )
                    _set_iteration_result(
                        iteration_node,
                        state_before,
                        state,
                        "direct_answer_finished",
                        TraceNodeStatus.SUCCEEDED,
                        step_id=step.id,
                    )
                    return

                tool_result = yield from self.tool_executor.execute(
                    state,
                    step,
                    decision.tool_calls,
                    usage,
                    mode,
                )
                status_by_result = {
                    "continue": TraceNodeStatus.SUCCEEDED,
                    "suspended": TraceNodeStatus.WAITING,
                    "finished": TraceNodeStatus.SUCCEEDED,
                    "failed": TraceNodeStatus.FAILED,
                    "cancelled": TraceNodeStatus.CANCELLED,
                }
                _set_iteration_result(
                    iteration_node,
                    state_before,
                    state,
                    f"tools_{tool_result.status.value}",
                    status_by_result[tool_result.status.value],
                    step_id=step.id,
                    tool_call_count=len(decision.tool_calls),
                )
                if tool_result.terminal:
                    return

    def _budget_exhausted(
        self,
        state: AgentRuntimeState,
        *,
        reason: str | None = None,
        error_class: str | None = None,
    ) -> Iterator[RenderEvent]:
        """预算耗尽：有执行结果则软收口，否则硬失败。"""

        ctx = state.context
        execution = ctx.state.get("last_execution")
        if isinstance(execution, dict) and execution.get("sql"):
            close_unfinished_tool_calls(state.messages)
            answer = (
                "预算已达上限，以下基于已成功执行的查询结果作答。"
                f" 行数={execution.get('row_count')}，字段={execution.get('fields')}。"
            )
            yield from self.lifecycle.finish(
                state,
                answer=answer,
                chart={},
                sql=execution.get("sql"),
                full_data=ctx.state.get("full_data"),
                execution=execution,
            )
            return
        yield from self.lifecycle.fail(
            state,
            reason or "预算已耗尽",
            error_class or AgentErrorClass.BUDGET.value,
        )

    def _emit(
        self,
        state: AgentRuntimeState,
        event_type: str,
        payload: dict[str, Any],
        step_id: int | None = None,
    ) -> RenderEvent:
        event = self.event_publisher.publish(
            state.require_run_id(),
            event_type,
            payload,
            step_id=step_id,
        )
        self.session.commit()
        return event


def _trace_runtime_snapshot(state: AgentRuntimeState) -> dict[str, Any]:
    """提取足以判断循环进展、且不会复制大结果集的运行状态。"""

    context = state.context.state
    return {
        "run_status": state.run.status,
        "record_status": state.record.status,
        "budget": state.budget_snapshot(),
        "message_count": len(state.messages),
        "state_revision": int(context.get("state_revision") or 0),
        "state_keys": sorted(context),
        "has_semantic_scope": isinstance(context.get("semantic_scope"), dict),
        "has_compiled_sql": bool(context.get("compiled_sql")),
        "has_validated_sql": bool(context.get("validated_sql")),
        "has_execution": isinstance(context.get("last_execution"), dict),
    }


def _set_iteration_result(
    node: TraceNodeHandle,
    before: dict[str, Any],
    state: AgentRuntimeState,
    outcome: str,
    status: TraceNodeStatus,
    **summary: Any,
) -> None:
    """统一收口每轮 ReAct 的结果和状态变化摘要。"""

    after = _trace_runtime_snapshot(state)
    node.set_status(status)
    node.set_output({"outcome": outcome, **summary})
    node.set_state_diff(before, after)
    node.set_output_detail({"runtime_state": after})


def _allows_direct_answer(state: AgentRuntimeState) -> bool:
    """闲聊可直接回答；问数必须已经存在成功执行结果。"""

    understanding = state.context.state.get("question_understanding")
    is_chitchat = (
        isinstance(understanding, dict)
        and understanding.get("category") == "chitchat"
    )
    return is_chitchat or bool(state.context.state.get("last_execution"))


# ---- Trace 结果 ----


def _trace_terminal_result(
    events: Iterator[RenderEvent],
    node: TraceNodeHandle,
) -> Generator[
    RenderEvent,
    None,
    TraceNodeStatus | None,
]:
    """观察业务终止事件更新 Trace，但不拦截或延迟 SSE 事件。"""

    terminal_status: TraceNodeStatus | None = None
    for event in events:
        if event.domain == "run.failed":
            node.set_attribute("gen_ai.agent.result", "failed")
            node.set_status(TraceNodeStatus.FAILED)
            terminal_status = TraceNodeStatus.FAILED
        elif event.domain == "run.finished":
            node.set_attribute("gen_ai.agent.result", "finished")
            node.set_status(TraceNodeStatus.SUCCEEDED)
            terminal_status = TraceNodeStatus.SUCCEEDED
        elif event.domain == "run.cancelled":
            node.set_attribute("gen_ai.agent.result", "cancelled")
            node.set_status(TraceNodeStatus.CANCELLED)
            terminal_status = TraceNodeStatus.CANCELLED
        elif event.domain == "clarification.required":
            node.set_attribute("gen_ai.agent.result", "waiting")
            node.set_status(TraceNodeStatus.WAITING)
            terminal_status = TraceNodeStatus.WAITING
        yield event
    return terminal_status
