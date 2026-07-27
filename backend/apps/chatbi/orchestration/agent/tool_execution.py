"""Function Calling ReAct 的工具执行与 Observation 处理。"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

import orjson
from langchain_core.messages import ToolMessage

from apps.chatbi.models import AgentClarificationResumeKind
from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.event import EventPublisher, RenderEvent
from apps.tool import (
    ToolCallRequest,
    ToolOutput,
    ToolRegistry,
    ToolStatus,
    batch_tool_calls,
    close_unfinished_tool_calls,
    execute_tool_batch,
    format_tool_message_content,
    maybe_offload_output,
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
    ) -> None:
        self._session = session
        self._config = config
        self._registry = registry
        self._tracer = tracer
        self._lifecycle = lifecycle
        self._event_publisher = event_publisher

    def execute(
        self,
        state: AgentRuntimeState,
        step: Any,
        calls: list[ToolCallRequest],
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
                lambda name, raw_args: self._execute_one(
                    context,
                    step,
                    mode,
                    name,
                    raw_args,
                ),
                max_workers=int(
                    getattr(self._config, "tool_parallel_workers", 4) or 4
                ),
            )

            for call, output in executed:
                output = maybe_offload_output(
                    output,
                    store=offload_store,
                    tool_name=call.name,
                    max_chars=int(
                        getattr(self._config, "summary_max_chars", 4000) or 4000
                    ),
                )
                tool_name = call.name
                call_id = call.call_id

                if tool_name == "clarify" and output.success:
                    clarify_verdict = budget.record_clarification()
                    if not clarify_verdict.allowed:
                        state.messages.append(
                            ToolMessage(
                                content=(
                                    "澄清次数已达上限，请基于现有信息继续，或如实说明无法完成。"
                                ),
                                tool_call_id=call_id,
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
                        output,
                        call_id,
                        step.id,
                        resume_kind=AgentClarificationResumeKind.AGENT_TOOL,
                        resume_payload={},
                    )
                    return ToolExecutionResult(ToolExecutionStatus.SUSPENDED)

                state.messages.append(
                    ToolMessage(
                        content=format_tool_message_content(
                            output.summary,
                            offload_ref=output.offload_ref,
                        ),
                        tool_call_id=call_id,
                    )
                )

                if tool_name == "finish" and output.success:
                    close_unfinished_tool_calls(state.messages)
                    agent_run_repository.finish_step(
                        self._session,
                        step,
                        {"tool": "finish"},
                        usage,
                    )
                    payload = output.payload
                    if payload.get("chart"):
                        yield self._publish(
                            state,
                            "chart-generated",
                            {"record_id": record.id, "chart": payload["chart"]},
                            step.id,
                        )
                    yield from self._lifecycle.finish(
                        state,
                        answer=payload.get("answer") or "",
                        chart=payload.get("chart") or {},
                        sql=payload.get("sql"),
                        step_id=step.id,
                        full_data=context.state.get("full_data"),
                        execution=context.state.get("last_execution"),
                    )
                    return ToolExecutionResult(ToolExecutionStatus.FINISHED)

                result_summary = _result_summary(tool_name, output)
                if output.success:
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
                        output.summary[:500],
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
                for event_type, payload in _semantic_events(
                    tool_name,
                    output,
                    record.id,
                ):
                    yield self._publish(state, event_type, payload, step.id)

                if (
                    tool_name == "execute_sql"
                    and not output.success
                    and output.status != ToolStatus.DENIED
                ):
                    retry = budget.record_sql_failure()
                    if not retry.allowed:
                        yield from self._lifecycle.fail(
                            state,
                            cast(str, retry.reason),
                            cast(str, retry.error_class),
                        )
                        return ToolExecutionResult(ToolExecutionStatus.FAILED)

        agent_run_repository.update_run(
            self._session,
            state.run,
            messages=state.serialized_messages(),
            budget_snapshot=budget.snapshot(),
            derived_state=state.persistable_context(),
        )
        self._session.commit()
        return ToolExecutionResult(ToolExecutionStatus.CONTINUE)

    def _execute_one(
        self,
        context: AgentToolContext,
        step: Any,
        mode: str,
        name: str,
        raw_args: dict[str, Any],
    ) -> ToolOutput:
        with self._tracer.span(
            "execute_tool",
            tool_attributes(tool_name=name, step_id=step.id),
        ) as tool_span:
            if mode == "soft" and name not in {"finish", "clarify"}:
                output = ToolOutput.denied(
                    f"预算接近上限，禁止调用 {name}。请 finish 或 clarify。",
                    error_code="budget_soft_tool_blocked",
                )
            else:
                output = self._registry.execute(name, context, raw_args)
            tool_span.set_attribute(
                "gen_ai.tool.call.result",
                getattr(output.status, "value", output.status),
            )
            return output

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


def _result_summary(tool_name: str, output: ToolOutput) -> dict[str, Any]:
    status = getattr(output.status, "value", output.status)
    base = {
        "success": bool(output.success),
        "status": status,
    }
    if output.offload_ref:
        base["offload_ref"] = output.offload_ref
    if not output.success:
        base["error_code"] = output.error_code
        return base
    payload = output.payload
    if tool_name == "execute_sql":
        return {
            **base,
            "row_count": payload.get("row_count"),
            "fields": payload.get("fields"),
        }
    if tool_name in {"compile_semantic_sql", "validate_sql"}:
        return {**base, "sql": payload.get("sql")}
    if tool_name == "search_semantic_assets":
        return {
            **base,
            "status": payload.get("status") or status,
            "metrics": payload.get("metrics"),
            "dimensions": payload.get("dimensions"),
            "tables": payload.get("tables"),
        }
    if tool_name == "get_dataset_schema":
        return {**base, "table_count": payload.get("table_count")}
    if tool_name in {"search_terminology", "get_sql_examples"}:
        return {**base, "count": payload.get("count")}
    return base


def _semantic_events(
    tool_name: str,
    output: ToolOutput,
    record_id: int,
) -> list[tuple[str, dict[str, Any]]]:
    if not output.success:
        return []
    payload = output.payload
    if tool_name == "compile_semantic_sql":
        return [("sql-generated", {"record_id": record_id, "sql": payload.get("sql")})]
    if tool_name == "validate_sql":
        return [("sql-validated", {"record_id": record_id, "sql": payload.get("sql")})]
    if tool_name == "execute_sql":
        return [
            (
                "sql-executed",
                {
                    "record_id": record_id,
                    "row_count": payload.get("row_count"),
                    "fields": payload.get("fields"),
                },
            )
        ]
    return []


__all__ = [
    "AgentToolExecutor",
    "ToolExecutionResult",
    "ToolExecutionStatus",
]
