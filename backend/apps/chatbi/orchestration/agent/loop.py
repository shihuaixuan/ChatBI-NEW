"""AgentLoop：LLM 自主规划 + 受控工具循环。

LLM 拥有：选择工具、组织参数、决定顺序、决定何时澄清与结束。
LLM 没有：越出白名单、绕过守护、超出预算（BudgetGuard 硬/软上限）。
状态即消息历史：run.messages 持久化除 system 外的全部消息，恢复=反序列化继续。
工具运行时内核见 apps.tool；本模块只负责 ChatBI 编排策略。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from apps.chatbi.errors import QuestionUnderstandingError
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
    AgentSpan,
    AgentTracer,
    agent_attributes,
)

__all__ = ["AgentLoop"]


class AgentLoop:
    def __init__(
        self,
        session: Any,
        *,
        event_publisher: EventPublisher,
        tracer: AgentTracer,
        lifecycle: AgentLifecycle,
        reasoner: AgentReasoner,
        tool_executor: AgentToolExecutor,
        input_preparer: AgentInputPreparer,
        state_factory: AgentRuntimeStateFactory,
    ) -> None:
        self.session = session
        self.event_publisher = event_publisher
        self.tracer = tracer
        self.lifecycle = lifecycle
        self.reasoner = reasoner
        self.tool_executor = tool_executor
        self.input_preparer = input_preparer
        self.state_factory = state_factory

    # ---- 入口 ----

    def run(self, run: ChatbiAgentRun, record: Any) -> Iterator[RenderEvent]:
        """在完整生成器生命周期内记录一次 Agent 调用。"""

        with self.tracer.span(
            "invoke_agent",
            agent_attributes(
                run_id=run.id or 0,
                record_id=record.id or 0,
                chat_id=run.chat_id,
            ),
        ) as span:
            yield from _trace_terminal_result(self._run(run, record), span)

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

        with self.tracer.span(
            "invoke_agent",
            agent_attributes(
                run_id=run.id or 0,
                record_id=record.id or 0,
                chat_id=run.chat_id,
            ),
        ) as span:
            yield from _trace_terminal_result(
                self._resume(run, record, clarification, answer_text),
                span,
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
            mode = budget.planning_mode()
            if mode == "exhausted":
                yield from self._budget_exhausted(state)
                return
            if mode == "soft" and not self.reasoner.available_tool_names(
                state,
                mode,
            ):
                yield from self._budget_exhausted(state)
                return

            verdict = budget.check_before_step()
            if not verdict.allowed:
                yield from self._budget_exhausted(
                    state,
                    reason=verdict.reason,
                    error_class=verdict.error_class,
                )
                return

            step_index = budget.steps + 1
            step = agent_run_repository.start_step(self.session, run, step_index, None, {})
            self.session.commit()
            yield self._emit(state, "step-started", {"record_id": record.id, "step_index": step_index}, step.id)

            decision = self.reasoner.decide(state, mode)
            usage = decision.usage
            text = decision.reasoning
            if text:
                yield self._emit(state, "thinking", {"record_id": record.id, "content": text}, step.id)

            if decision.is_direct_answer:
                if not _allows_direct_answer(state):
                    close_unfinished_tool_calls(messages)
                    agent_run_repository.fail_step(
                        self.session,
                        step,
                        "问数消息没有成功查询结果，禁止直接回答",
                    )
                    yield from self.lifecycle.fail(
                        state,
                        "问数消息必须成功执行查询后才能结束，禁止生成看似来自数据库的直接回答。",
                        AgentErrorClass.SQL.value,
                    )
                    return
                close_unfinished_tool_calls(messages)
                agent_run_repository.finish_step(self.session, step, {"mode": "direct_answer"}, usage)
                yield from self.lifecycle.finish(
                    state,
                    answer=text or "（模型未给出回答）",
                    chart={},
                    sql=(ctx.state.get("last_execution") or {}).get("sql"),
                    step_id=step.id,
                    full_data=ctx.state.get("full_data"),
                    execution=ctx.state.get("last_execution"),
                )
                return

            requests = decision.tool_calls
            tool_result = yield from self.tool_executor.execute(
                state,
                step,
                requests,
                usage,
                mode,
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
        return self.event_publisher.publish(
            state.require_run_id(),
            event_type,
            payload,
            step_id=step_id,
        )


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
    span: AgentSpan,
) -> Iterator[RenderEvent]:
    """依据产品终止事件标记 span 结果，不让 Trace 反向控制事件流。"""

    for event in events:
        if event.domain == "run.failed":
            span.set_attribute("gen_ai.agent.result", "failed")
        elif event.domain == "run.finished":
            span.set_attribute("gen_ai.agent.result", "finished")
        yield event
