"""Function Calling ReAct 的工具执行与 Observation 处理。"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

import orjson

from apps.chatbi.models import AgentClarificationResumeKind
from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.messages import (
    AgentMessage,
    close_unfinished_tool_calls,
    format_tool_message_content,
    maybe_offload_result,
)
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tool_results import (
    ChatBIToolResultProcessor,
    ToolControlAction,
)
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.event import EventPublisher, RenderEvent
from apps.tool import (
    ToolCall,
    ToolErrorCategory,
    ToolRegistry,
    ToolResult,
    ToolStatus,
    batch_tool_calls,
    execute_tool_batch,
)
from apps.trace import AgentTracer, tool_attributes


class ToolExecutionStatus(StrEnum):
    """一轮工具执行完成后交还主循环的状态。"""

    CONTINUE = "continue"
    SUSPENDED = "suspended"
    FINISHED = "finished"
    FAILED = "failed"


@dataclass(frozen=True)
class ToolExecutionResult:
    status: ToolExecutionStatus

    @property
    def terminal(self) -> bool:
        return self.status != ToolExecutionStatus.CONTINUE


class AgentToolExecutor:
    """执行工具批次，并把结果转换为 Observation 或生命周期出口。"""

    def __init__(
        self,
        session: Any,
        config: AgentConfig,
        registry: ToolRegistry,
        tracer: AgentTracer,
        lifecycle: AgentLifecycle,
        event_publisher: EventPublisher,
        result_processor: ChatBIToolResultProcessor,
    ) -> None:
        self._session = session
        self._config = config
        self._registry = registry
        self._tracer = tracer
        self._lifecycle = lifecycle
        self._event_publisher = event_publisher
        self._result_processor = result_processor

    def execute(
        self,
        state: AgentRuntimeState,
        step: Any,
        calls: list[ToolCall],
        usage: dict[str, Any],
        mode: str,
    ) -> Generator[RenderEvent, None, ToolExecutionResult]:
        """执行一轮 Action，并按调用顺序发布产品事件。"""

        if not calls:
            raise ValueError("AGENT_TOOL_CALLS_REQUIRED")
        if mode not in {"normal", "soft"}:
            raise ValueError(f"Unsupported tool execution mode: {mode}")

        record = state.record
        context = state.context
        budget = state.budget
        batches = batch_tool_calls(calls, self._registry.get)
        offload_store = context.state.setdefault("tool_offloads", {})

        for batch in batches:
            for call in batch:
                fuse = budget.check_tool_call(call.name, call.args)
                if not fuse.allowed:
                    step.tool_name = call.name
                    step.args_summary = _tool_args_summary(call.name, call.args, context)
                    self._session.add(step)
                    agent_run_repository.fail_step(
                        self._session,
                        step,
                        cast(str, fuse.reason),
                    )
                    yield from self._lifecycle.fail(
                        state,
                        cast(str, fuse.reason),
                        cast(str, fuse.error_class),
                    )
                    return ToolExecutionResult(ToolExecutionStatus.FAILED)
                if call.name == "execute_sql":
                    sql_verdict = state.chatbi_budget.check_sql_call(
                        str(call.args.get("sql") or "")
                    )
                    if not sql_verdict.allowed:
                        yield from self._lifecycle.fail(
                            state,
                            cast(str, sql_verdict.reason),
                            cast(str, sql_verdict.error_class),
                        )
                        return ToolExecutionResult(ToolExecutionStatus.FAILED)

            for call in batch:
                step.tool_name = call.name
                step.args_summary = _tool_args_summary(call.name, call.args, context)
                self._session.add(step)
                yield self._publish(
                    state,
                    "tool-called",
                    {
                        "record_id": record.id,
                        "tool_name": call.name,
                        "args_summary": step.args_summary,
                    },
                    step.id,
                )

            executed = execute_tool_batch(
                batch,
                lambda call: self._execute_one(
                    context,
                    step,
                    mode,
                    call,
                ),
                max_workers=int(
                    getattr(self._config, "tool_parallel_workers", 4) or 4
                ),
            )

            for call, result in executed:
                projection = self._result_processor.process(
                    context,
                    call.name,
                    result,
                )
                result = projection.result
                context.state.update(projection.state_patch)
                result = maybe_offload_result(
                    result,
                    store=offload_store,
                    tool_name=call.name,
                    max_chars=int(
                        getattr(self._config, "summary_max_chars", 4000) or 4000
                    ),
                )
                tool_name = call.name
                call_id = call.call_id
                data = _result_data(result)

                if (
                    projection.control == ToolControlAction.CLARIFY
                    and result.status == ToolStatus.SUCCEEDED
                ):
                    clarify_verdict = state.chatbi_budget.record_clarification()
                    if not clarify_verdict.allowed:
                        state.messages.append(
                            AgentMessage.tool(
                                "澄清次数已达上限，请基于现有信息继续，或如实说明无法完成。",
                                call_id,
                            )
                        )
                        agent_run_repository.finish_step(
                            self._session,
                            step,
                            {"tool": "clarify", "rejected": "budget"},
                            usage,
                        )
                        continue
                    agent_run_repository.finish_step(
                        self._session,
                        step,
                        {"tool": "clarify"},
                        usage,
                    )
                    yield self._lifecycle.suspend(
                        state,
                        str(data["question"]),
                        list(data.get("options") or []),
                        call_id,
                        step.id,
                        resume_kind=AgentClarificationResumeKind.AGENT_TOOL,
                        resume_payload={},
                    )
                    return ToolExecutionResult(ToolExecutionStatus.SUSPENDED)

                state.messages.append(
                    AgentMessage.tool(
                        format_tool_message_content(
                            result.model_content,
                            offload_ref=_offload_ref(result),
                        ),
                        call_id,
                    )
                )

                if (
                    projection.control == ToolControlAction.FINISH
                    and result.status == ToolStatus.SUCCEEDED
                ):
                    close_unfinished_tool_calls(state.messages)
                    agent_run_repository.finish_step(
                        self._session,
                        step,
                        {"tool": "finish"},
                        usage,
                    )
                    if data.get("chart"):
                        yield self._publish(
                            state,
                            "chart-generated",
                            {"record_id": record.id, "chart": data["chart"]},
                            step.id,
                        )
                    yield from self._lifecycle.finish(
                        state,
                        answer=data.get("answer") or "",
                        chart=data.get("chart") or {},
                        sql=data.get("sql"),
                        step_id=step.id,
                        full_data=context.state.get("full_data"),
                        execution=context.state.get("last_execution"),
                    )
                    return ToolExecutionResult(ToolExecutionStatus.FINISHED)

                result_summary = projection.audit_summary
                if result.status == ToolStatus.SUCCEEDED:
                    agent_run_repository.finish_step(
                        self._session,
                        step,
                        result_summary,
                        usage,
                    )
                else:
                    agent_run_repository.fail_step(
                        self._session,
                        step,
                        result.model_content[:500],
                    )
                yield self._publish(
                    state,
                    "tool-result",
                    {
                        "record_id": record.id,
                        "tool_name": tool_name,
                        **result_summary,
                    },
                    step.id,
                )
                for event in projection.events:
                    yield self._publish(
                        state,
                        event.event_type,
                        {"record_id": record.id, **event.payload},
                        step.id,
                    )

                if tool_name == "execute_sql":
                    state.chatbi_budget.record_sql_result(
                        str(call.args.get("sql") or ""),
                        result,
                    )

        agent_run_repository.update_run(
            self._session,
            state.run,
            messages=state.serialized_messages(),
            budget_snapshot=state.budget_snapshot(),
            derived_state=state.persistable_context(),
        )
        self._session.commit()
        return ToolExecutionResult(ToolExecutionStatus.CONTINUE)

    def _execute_one(
        self,
        context: AgentToolContext,
        step: Any,
        mode: str,
        call: ToolCall,
    ) -> ToolResult[Any]:
        with self._tracer.span(
            "execute_tool",
            tool_attributes(tool_name=call.name, step_id=step.id),
        ) as tool_span:
            if mode == "soft" and call.name not in {"finish", "clarify"}:
                result = ToolResult.rejected(
                    f"预算接近上限，禁止调用 {call.name}。请 finish 或 clarify。",
                    error_code="budget_soft_tool_blocked",
                    error_category=ToolErrorCategory.BUSINESS_RULE,
                )
            else:
                result = self._registry.execute(call, context)
            tool_span.set_attribute(
                "gen_ai.tool.call.result",
                result.status.value,
            )
            return result

    def _publish(
        self,
        state: AgentRuntimeState,
        event_type: str,
        payload: dict[str, Any],
        step_id: int | None = None,
    ) -> RenderEvent:
        return self._event_publisher.publish(
            state.require_run_id(),
            event_type,
            payload,
            step_id=step_id,
        )


def _args_summary(args: dict[str, Any]) -> dict[str, Any]:
    encoded = orjson.dumps(args).decode()
    if len(encoded) > 2000:
        return {"_truncated": encoded[:2000]}
    return args


def _tool_args_summary(
    tool_name: str,
    raw_args: dict[str, Any],
    context: AgentToolContext,
) -> dict[str, Any]:
    """记录工具真正消费的业务输入，避免无参工具在时间线中显示为空。"""

    if tool_name != "search_semantic_assets":
        return _args_summary(raw_args)
    understanding = context.state.get("question_understanding")
    if not isinstance(understanding, dict):
        return {}
    return _args_summary(
        {
            "rewritten_question": understanding.get("rewritten_question"),
            "intent": understanding.get("intent") or {},
        }
    )


def _result_data(result: ToolResult[Any]) -> dict[str, Any]:
    if result.data is None:
        return {}
    return result.data.model_dump(mode="json")


def _offload_ref(result: ToolResult[Any]) -> str | None:
    value = result.metadata.get("offload_ref")
    return str(value) if value else None


__all__ = [
    "AgentToolExecutor",
    "ToolExecutionResult",
    "ToolExecutionStatus",
]
