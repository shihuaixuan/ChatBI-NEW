"""Function Calling ReAct 的工具执行与 Observation 处理。"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic, perf_counter
from typing import Any, cast

import orjson

from apps.chatbi.errors import AgentActionError
from apps.chatbi.models import AgentClarificationResumeKind, AgentToolCallStatus
from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.cancellation import CancellationStage
from apps.chatbi.orchestration.agent.cancellation_guard import CancellationGuard
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
from apps.chatbi.orchestration.agent.tool_visibility import (
    STANDARD_TOOLS,
    visible_tool_names,
)
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.orchestration.agent.working_state import (
    progress_name,
    project_tool_observation,
    recommended_actions,
)
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.event import EventPublisher, RenderEvent
from apps.tool import (
    RetryAdvice,
    ToolBatchExecutionError,
    ToolCall,
    ToolCallContext,
    ToolErrorCategory,
    ToolRegistry,
    ToolResult,
    ToolStatus,
    batch_tool_calls,
    effective_timeout_seconds,
    execute_tool_batch,
)
from apps.trace import (
    AgentTraceRecorder,
    TraceNodeSpec,
    TraceNodeStatus,
    TraceNodeType,
    tool_attributes,
)

_MISSING = object()


class ToolExecutionStatus(StrEnum):
    """一轮工具执行完成后交还主循环的状态。"""

    CONTINUE = "continue"
    SUSPENDED = "suspended"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
        recorder: AgentTraceRecorder,
        lifecycle: AgentLifecycle,
        event_publisher: EventPublisher,
        result_processor: ChatBIToolResultProcessor,
    ) -> None:
        self._session = session
        self._config = config
        self._registry = registry
        self._recorder = recorder
        self._lifecycle = lifecycle
        self._event_publisher = event_publisher
        self._result_processor = result_processor

    def _check_action(
        self,
        state: AgentRuntimeState,
        call: ToolCall,
        mode: str,
    ) -> ToolResult[Any] | None:
        """阻止标准工具越过当前可信进展，并把纠错方向返回给模型。"""

        # clarify 是模型发现新歧义时的主动中断能力，不按普通数据阶段拒绝。
        if call.name == "clarify" or call.name not in STANDARD_TOOLS:
            return None
        allowed = visible_tool_names(state, mode, self._registry.names())
        if call.name in allowed:
            return None
        progress = progress_name(state.context.state)
        recommended = recommended_actions(state.context.state)
        content = {
            "status": "rejected",
            "error_code": AgentActionError.ACTION_NOT_AVAILABLE,
            "message": f"工具 {call.name} 不适用于当前进展 {progress}",
            "current_progress": progress,
            "recommended_actions": recommended,
            "retry_same_tool": False,
        }
        return ToolResult.rejected(
            orjson.dumps(content).decode(),
            error_code=AgentActionError.ACTION_NOT_AVAILABLE,
            error_category=ToolErrorCategory.BUSINESS_RULE,
            details={
                "current_progress": progress,
                "available_tools": allowed,
                "recommended_actions": recommended,
            },
        )

    @staticmethod
    def _record_observation(
        context: AgentToolContext,
        observation: dict[str, Any],
    ) -> None:
        """只保留近期结构化反馈，供模型纠错和恢复审计。"""

        bounded = _bounded_summary(observation)
        context.state["last_tool_observation"] = bounded
        history = context.state.setdefault("tool_observation_history", [])
        if not isinstance(history, list):
            raise TypeError("AGENT_TOOL_OBSERVATION_HISTORY_INVALID")
        history.append(bounded)
        del history[:-20]

    @staticmethod
    def _model_observation_content(
        model_content: str,
        observation: dict[str, Any],
    ) -> str:
        """在原工具内容后追加统一纠错信息，避免模型自行猜测错误含义。"""

        encoded = orjson.dumps(_bounded_summary(observation)).decode()
        if not model_content:
            return f"<tool-observation>{encoded}</tool-observation>"
        return f"{model_content}\n<tool-observation>{encoded}</tool-observation>"

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
        tool_call_rows: dict[str, Any] = {}
        completed_count = 0
        failed_count = 0

        # 模型一次返回的每个 Tool Call 都先建立独立事实记录和开始事件。
        for call in calls:
            if not call.call_id:
                raise ValueError("AGENT_TOOL_CALL_ID_REQUIRED")
            args_summary = _tool_args_summary(call.name, call.args, context)
            with self._recorder.node(
                TraceNodeSpec(
                    run_id=state.require_run_id(),
                    node_key=f"tool_call_started:{step.id}:{call.call_id}",
                    node_type=TraceNodeType.PERSISTENCE,
                    name="persist_tool_call_started",
                    display_name=f"创建工具调用记录：{call.name}",
                    attributes=tool_attributes(
                        tool_name=call.name,
                        run_id=state.require_run_id(),
                        step_id=step.id,
                        tool_call_id=call.call_id,
                    ),
                ),
                input_data={
                    "tool_name": call.name,
                    "tool_call_id": call.call_id,
                },
                input_detail={"args_summary": args_summary},
            ) as persistence_node:
                tool_call_rows[call.call_id] = (
                    agent_run_repository.start_tool_call(
                        self._session,
                        run_id=state.require_run_id(),
                        step_id=step.id,
                        tool_call_id=call.call_id,
                        tool_name=call.name,
                        args_summary=args_summary,
                    )
                )
                persistence_node.set_output(
                    {
                        "tool_call_id": call.call_id,
                        "status": AgentToolCallStatus.RUNNING.value,
                    }
                )
                yield self._publish(
                    state,
                    "tool-called",
                    {
                        "record_id": record.id,
                        "tool_call_id": call.call_id,
                        "step_id": step.id,
                        "tool_name": call.name,
                        "status": AgentToolCallStatus.RUNNING.value,
                        "args_summary": args_summary,
                    },
                    step.id,
                )

        for batch in batches:
            cancellation_guard = CancellationGuard(state.cancellation)
            if cancellation_guard.check(CancellationStage.BEFORE_TOOL):
                yield from self._interrupt_open_tool_calls(
                    state,
                    step,
                    calls,
                    tool_call_rows,
                    reason="用户已请求取消，工具未开始执行",
                )
                agent_run_repository.cancel_step(
                    self._session,
                    step,
                    "用户在工具开始前请求取消运行",
                )
                self._session.commit()
                yield from self._lifecycle.cancel(
                    state,
                    "用户在工具开始前请求取消运行",
                )
                return ToolExecutionResult(ToolExecutionStatus.CANCELLED)
            executable_batch: list[ToolCall] = []
            for call in batch:
                with self._recorder.node(
                    TraceNodeSpec(
                        run_id=state.require_run_id(),
                        node_key=f"action_guard:{step.id}:{call.call_id}",
                        node_type=TraceNodeType.VALIDATION,
                        name="validate_tool_action",
                        display_name=f"校验工具是否允许：{call.name}",
                        attributes=tool_attributes(
                            tool_name=call.name,
                            run_id=state.require_run_id(),
                            step_id=step.id,
                            tool_call_id=call.call_id,
                        ),
                    ),
                    input_data={"tool_name": call.name, "mode": mode},
                    input_detail={"args": call.args},
                ) as action_node:
                    action_rejection = self._check_action(state, call, mode)
                    action_node.set_output(
                        {
                            "allowed": action_rejection is None,
                            "error_code": (
                                action_rejection.error_code
                                if action_rejection is not None
                                else None
                            ),
                        }
                    )
                    if action_rejection is not None:
                        action_node.set_status(TraceNodeStatus.REJECTED)
                        action_node.set_output_detail(
                            {"rejection": _tool_result_detail(action_rejection)}
                        )
                if action_rejection is not None:
                    observation = project_tool_observation(
                        context.state,
                        call.name,
                        action_rejection,
                        state_changed=False,
                    )
                    self._record_observation(context, observation)
                    yield self._finish_tool_call_event(
                        state,
                        step,
                        tool_call_rows.pop(call.call_id),
                        action_rejection,
                        {
                            "success": False,
                            "status": action_rejection.status.value,
                            "error_code": action_rejection.error_code,
                            "recommended_actions": observation["recommended_actions"],
                        },
                    )
                    state.messages.append(
                        AgentMessage.tool(
                            self._model_observation_content(
                                action_rejection.model_content,
                                observation,
                            ),
                            call.call_id,
                        )
                    )
                    failed_count += 1
                    continue
                with self._recorder.node(
                    TraceNodeSpec(
                        run_id=state.require_run_id(),
                        node_key=f"tool_budget_guard:{step.id}:{call.call_id}",
                        node_type=TraceNodeType.VALIDATION,
                        name="validate_tool_budget",
                        display_name=f"校验工具重复调用预算：{call.name}",
                        attributes=tool_attributes(
                            tool_name=call.name,
                            run_id=state.require_run_id(),
                            step_id=step.id,
                            tool_call_id=call.call_id,
                        ),
                    ),
                    input_data={"tool_name": call.name},
                    input_detail={"args": call.args},
                ) as budget_node:
                    fuse = budget.check_tool_call(call.name, call.args)
                    budget_node.set_output(
                        {
                            "allowed": fuse.allowed,
                            "reason": fuse.reason,
                            "error_class": fuse.error_class,
                        }
                    )
                    if not fuse.allowed:
                        budget_node.set_status(TraceNodeStatus.REJECTED)
                if not fuse.allowed:
                    result = ToolResult.rejected(
                        cast(str, fuse.reason),
                        error_code="tool_call_budget_rejected",
                        error_category=ToolErrorCategory.BUSINESS_RULE,
                    )
                    yield self._finish_tool_call_event(
                        state,
                        step,
                        tool_call_rows.pop(call.call_id),
                        result,
                        {
                            "success": False,
                            "status": result.status.value,
                            "error_code": result.error_code,
                        },
                    )
                    yield from self._interrupt_open_tool_calls(
                        state,
                        step,
                        calls,
                        tool_call_rows,
                        reason="当前运行已被工具调用预算终止",
                    )
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
                    with self._recorder.node(
                        TraceNodeSpec(
                            run_id=state.require_run_id(),
                            node_key=f"sql_budget_guard:{step.id}:{call.call_id}",
                            node_type=TraceNodeType.VALIDATION,
                            name="validate_sql_retry_budget",
                            display_name="校验 SQL 修正预算",
                            attributes=tool_attributes(
                                tool_name=call.name,
                                run_id=state.require_run_id(),
                                step_id=step.id,
                                tool_call_id=call.call_id,
                            ),
                        ),
                        input_data={"tool_call_id": call.call_id},
                        input_detail={"sql": call.args.get("sql")},
                    ) as sql_budget_node:
                        sql_verdict = state.chatbi_budget.check_sql_call(
                            str(call.args.get("sql") or "")
                        )
                        sql_budget_node.set_output(
                            {
                                "allowed": sql_verdict.allowed,
                                "reason": sql_verdict.reason,
                                "error_class": sql_verdict.error_class,
                            }
                        )
                        if not sql_verdict.allowed:
                            sql_budget_node.set_status(TraceNodeStatus.REJECTED)
                    if not sql_verdict.allowed:
                        result = ToolResult.rejected(
                            cast(str, sql_verdict.reason),
                            error_code="sql_correction_budget_rejected",
                            error_category=ToolErrorCategory.BUSINESS_RULE,
                        )
                        yield self._finish_tool_call_event(
                            state,
                            step,
                            tool_call_rows.pop(call.call_id),
                            result,
                            {
                                "success": False,
                                "status": result.status.value,
                                "error_code": result.error_code,
                            },
                        )
                        yield from self._interrupt_open_tool_calls(
                            state,
                            step,
                            calls,
                            tool_call_rows,
                            reason="当前运行已被 SQL 修正预算终止",
                        )
                        agent_run_repository.fail_step(
                            self._session,
                            step,
                            cast(str, sql_verdict.reason),
                        )
                        yield from self._lifecycle.fail(
                            state,
                            cast(str, sql_verdict.reason),
                            cast(str, sql_verdict.error_class),
                        )
                        return ToolExecutionResult(ToolExecutionStatus.FAILED)
                executable_batch.append(call)

            if not executable_batch:
                continue

            try:
                executed = execute_tool_batch(
                    executable_batch,
                    lambda call: self._execute_one(
                        context,
                        state,
                        step,
                        call,
                    ),
                    max_workers=int(
                        getattr(self._config, "tool_parallel_workers", 4) or 4
                    ),
                    cancellation=state.cancellation,
                )
            except ToolBatchExecutionError as exc:
                for call, outcome in exc.outcomes:
                    if isinstance(outcome, ToolResult):
                        result = outcome
                        summary = {
                            "success": result.status == ToolStatus.SUCCEEDED,
                            "status": result.status.value,
                            "error_code": result.error_code,
                        }
                    else:
                        result = ToolResult.failed(
                            "工具执行发生未声明异常。",
                            error_code="tool_undeclared_exception",
                            error_category=ToolErrorCategory.CONFIGURATION,
                            retry_advice=RetryAdvice.NEVER,
                        )
                        summary = {
                            "success": False,
                            "status": result.status.value,
                            "error_code": result.error_code,
                        }
                    row = tool_call_rows.pop(call.call_id, None)
                    if row is not None:
                        yield self._finish_tool_call_event(
                            state,
                            step,
                            row,
                            result,
                            summary,
                        )
                yield from self._interrupt_open_tool_calls(
                    state,
                    step,
                    calls,
                    tool_call_rows,
                    reason="工具执行发生未声明异常",
                )
                agent_run_repository.fail_step(
                    self._session,
                    step,
                    "工具执行发生未声明异常",
                )
                self._session.commit()
                raise exc.cause from exc
            except Exception:
                yield from self._interrupt_open_tool_calls(
                    state,
                    step,
                    calls,
                    tool_call_rows,
                    reason="工具执行发生未声明异常",
                )
                agent_run_repository.fail_step(
                    self._session,
                    step,
                    "工具执行发生未声明异常",
                )
                self._session.commit()
                raise

            for call, result in executed:
                if CancellationGuard(state.cancellation).check(
                    CancellationStage.AFTER_TOOL
                ):
                    result = _cancelled_tool_result(result)
                before_projection = {
                    "state_revision": int(context.state.get("state_revision") or 0),
                    "state_keys": sorted(context.state),
                }
                with self._recorder.node(
                    TraceNodeSpec(
                        run_id=state.require_run_id(),
                        node_key=f"tool_projection:{step.id}:{call.call_id}",
                        node_type=TraceNodeType.PROJECTION,
                        name="project_tool_result",
                        display_name=f"投影工具结果：{call.name}",
                        attributes=tool_attributes(
                            tool_name=call.name,
                            run_id=state.require_run_id(),
                            step_id=step.id,
                            tool_call_id=call.call_id,
                        ),
                    ),
                    input_data={
                        "tool_name": call.name,
                        "tool_call_id": call.call_id,
                        "status": result.status.value,
                    },
                    input_detail={"tool_result": _tool_result_detail(result)},
                ) as projection_node:
                    projection = self._result_processor.process(
                        context,
                        call.name,
                        result,
                    )
                    result = projection.result
                    state_changed = any(
                        context.state.get(key, _MISSING) != value
                        for key, value in projection.state_patch.items()
                    )
                    context.state.update(projection.state_patch)
                    if state_changed:
                        context.state["state_revision"] = (
                            int(context.state.get("state_revision") or 0) + 1
                        )
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
                            result = ToolResult.rejected(
                                "澄清次数已达上限，请基于现有信息继续，或如实说明无法完成。",
                                error_code="clarification_budget_rejected",
                                error_category=ToolErrorCategory.BUSINESS_RULE,
                            )
                            projection = self._result_processor.process(
                                context,
                                call.name,
                                result,
                            )

                    observation = project_tool_observation(
                        context.state,
                        call.name,
                        result,
                        state_changed=state_changed,
                    )
                    self._record_observation(context, observation)

                    result_summary = _bounded_summary(projection.audit_summary)
                    after_projection = {
                        "state_revision": int(
                            context.state.get("state_revision") or 0
                        ),
                        "state_keys": sorted(context.state),
                        "changed_keys": sorted(projection.state_patch),
                    }
                    projection_node.set_output(
                        {
                            "status": result.status.value,
                            "control": projection.control.value,
                            "state_changed": state_changed,
                            "changed_keys": sorted(projection.state_patch),
                            "error_code": result.error_code,
                        }
                    )
                    projection_node.set_state_diff(
                        before_projection,
                        after_projection,
                    )
                    projection_node.set_output_detail(
                        {
                            "projected_result": _tool_result_detail(result),
                            "state_patch": projection.state_patch,
                            "observation": observation,
                            "audit_summary": result_summary,
                        }
                    )
                    if result.status is not ToolStatus.SUCCEEDED:
                        projection_node.set_status(
                            TraceNodeStatus(result.status.value)
                        )
                    yield self._finish_tool_call_event(
                        state,
                        step,
                        tool_call_rows.pop(call_id),
                        result,
                        result_summary,
                    )
                if result.status == ToolStatus.SUCCEEDED:
                    completed_count += 1
                else:
                    failed_count += 1

                if result.error_category == ToolErrorCategory.CANCELLATION:
                    yield from self._interrupt_open_tool_calls(
                        state,
                        step,
                        calls,
                        tool_call_rows,
                        reason="当前运行已收到用户取消请求",
                    )
                    agent_run_repository.cancel_step(
                        self._session,
                        step,
                        result.model_content,
                    )
                    self._session.commit()
                    yield from self._lifecycle.cancel(
                        state,
                        result.model_content,
                    )
                    return ToolExecutionResult(ToolExecutionStatus.CANCELLED)

                if (
                    projection.control == ToolControlAction.CLARIFY
                    and result.status == ToolStatus.SUCCEEDED
                ):
                    yield from self._interrupt_open_tool_calls(
                        state,
                        step,
                        calls,
                        tool_call_rows,
                        reason="当前运行已进入澄清等待",
                    )
                    agent_run_repository.finish_step(
                        self._session,
                        step,
                        {
                            "tool_call_count": completed_count + failed_count,
                            "failed_tool_call_count": failed_count,
                        },
                        usage,
                    )
                    self._session.commit()
                    clarification_options = list(data.get("options") or [])
                    resume_payload = _agent_tool_resume_payload(
                        context.state,
                        clarification_options,
                    )
                    queued_clarifications = data.get("pending_clarifications")
                    if isinstance(queued_clarifications, list) and queued_clarifications:
                        resume_payload["pending_clarifications"] = queued_clarifications
                    yield self._lifecycle.suspend(
                        state,
                        str(data["question"]),
                        clarification_options,
                        call_id,
                        step.id,
                        resume_kind=AgentClarificationResumeKind.AGENT_TOOL,
                        resume_payload=resume_payload,
                    )
                    return ToolExecutionResult(ToolExecutionStatus.SUSPENDED)

                state.messages.append(
                    AgentMessage.tool(
                        format_tool_message_content(
                            self._model_observation_content(
                                result.model_content,
                                observation,
                            ),
                            offload_ref=_offload_ref(result),
                        ),
                        call_id,
                    )
                )

                if projection.control == ToolControlAction.REFUSE:
                    yield from self._interrupt_open_tool_calls(
                        state,
                        step,
                        calls,
                        tool_call_rows,
                        reason="当前问题无法在可执行语义范围内安全完成",
                    )
                    close_unfinished_tool_calls(state.messages)
                    agent_run_repository.finish_step(
                        self._session,
                        step,
                        {
                            "tool_call_count": completed_count + failed_count,
                            "failed_tool_call_count": failed_count,
                            "terminal_refusal": True,
                        },
                        usage,
                    )
                    self._session.commit()
                    yield from self._lifecycle.finish(
                        state,
                        answer=str(projection.control_data.get("answer") or ""),
                        chart={},
                        sql=None,
                        step_id=step.id,
                    )
                    return ToolExecutionResult(ToolExecutionStatus.FINISHED)

                if (
                    projection.control == ToolControlAction.FINISH
                    and result.status == ToolStatus.SUCCEEDED
                ):
                    yield from self._interrupt_open_tool_calls(
                        state,
                        step,
                        calls,
                        tool_call_rows,
                        reason="当前运行已完成",
                    )
                    close_unfinished_tool_calls(state.messages)
                    agent_run_repository.finish_step(
                        self._session,
                        step,
                        {
                            "tool_call_count": completed_count + failed_count,
                            "failed_tool_call_count": failed_count,
                        },
                        usage,
                    )
                    self._session.commit()
                    if data.get("chart"):
                        yield self._publish(
                            state,
                            "chart-generated",
                            {
                                "record_id": record.id,
                                "tool_call_id": call_id,
                                "step_id": step.id,
                                "chart": data["chart"],
                            },
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

                for event in projection.events:
                    yield self._publish(
                        state,
                        event.event_type,
                        {
                            "record_id": record.id,
                            "tool_call_id": call_id,
                            "step_id": step.id,
                            **_bounded_summary(event.payload),
                        },
                        step.id,
                    )

                if tool_name == "execute_sql":
                    state.chatbi_budget.record_sql_result(
                        str(call.args.get("sql") or ""),
                        result,
                    )

        with self._recorder.node(
            TraceNodeSpec(
                run_id=state.require_run_id(),
                node_key=f"step_snapshot:{step.id}",
                node_type=TraceNodeType.PERSISTENCE,
                name="persist_agent_step_snapshot",
                display_name="持久化步骤结果与运行快照",
                metadata={"step_id": step.id},
            ),
            input_data={
                "step_id": step.id,
                "tool_call_count": completed_count + failed_count,
                "failed_tool_call_count": failed_count,
            },
        ) as snapshot_node:
            agent_run_repository.finish_step(
                self._session,
                step,
                {
                    "tool_call_count": completed_count + failed_count,
                    "failed_tool_call_count": failed_count,
                },
                usage,
            )
            agent_run_repository.update_run(
                self._session,
                state.run,
                messages=state.serialized_messages(),
                budget_snapshot=state.budget_snapshot(),
                derived_state=state.persistable_context(),
            )
            self._session.commit()
            snapshot_node.set_output(
                {
                    "step_status": step.status,
                    "message_count": len(state.messages),
                    "state_revision": int(
                        state.context.state.get("state_revision") or 0
                    ),
                }
            )
        return ToolExecutionResult(ToolExecutionStatus.CONTINUE)

    def _execute_one(
        self,
        context: AgentToolContext,
        state: AgentRuntimeState,
        step: Any,
        call: ToolCall,
    ) -> ToolResult[Any]:
        started_at = perf_counter()
        tool = self._registry.get(call.name)
        declared_timeout = tool.execution.timeout_seconds if tool is not None else None
        timeout_seconds = effective_timeout_seconds(
            declared_timeout=declared_timeout,
            default_timeout=float(self._config.tool_default_timeout_seconds),
            maximum_timeout=float(self._config.tool_timeout_seconds),
            run_remaining=state.budget.remaining_seconds(),
        )
        call_context = ToolCallContext(
            tool_call_id=call.call_id,
            deadline_monotonic=monotonic() + timeout_seconds,
            cancellation=state.cancellation,
        )
        with self._recorder.node(
            TraceNodeSpec(
                run_id=state.require_run_id(),
                node_type=TraceNodeType.TOOL,
                name="execute_tool",
                display_name=f"工具执行：{call.name}",
                attributes=tool_attributes(
                    tool_name=call.name,
                    run_id=state.require_run_id(),
                    step_id=step.id,
                    tool_call_id=call.call_id,
                ),
                metadata={
                    "tool_name": call.name,
                    "tool_call_id": call.call_id,
                    "step_id": step.id,
                },
            ),
            input_data={
                "tool_name": call.name,
                "tool_call_id": call.call_id,
                "timeout_seconds": timeout_seconds,
                "arg_count": len(call.args),
            },
            input_detail={"args": call.args},
        ) as tool_node:
            if state.cancellation.is_cancelled():
                result = ToolResult.interrupted(
                    "用户已请求取消，工具未开始执行。",
                    error_code="tool_cancelled_before_start",
                    metadata={"underlying_operation_started": False},
                )
            elif timeout_seconds <= 0:
                result = ToolResult.interrupted(
                    "运行截止时间已到，工具未开始执行。",
                    error_code="tool_deadline_exceeded_before_start",
                    error_category=ToolErrorCategory.TIMEOUT,
                    metadata={"underlying_operation_started": False},
                )
            else:
                result = self._registry.execute(
                    call,
                    context,
                    call_context=call_context,
                )
                if state.cancellation.is_cancelled():
                    supports_cancellation = bool(
                        tool and tool.execution.supports_cancellation
                    )
                    result = ToolResult.interrupted(
                        (
                            "用户取消请求已生效。"
                            if supports_cancellation
                            else "用户已请求取消；该工具不支持执行中取消，底层操作会继续到返回，此时可能已经完成。"
                        ),
                        error_code="tool_cancelled",
                        metadata={
                            "underlying_operation_may_have_completed": (
                                not supports_cancellation
                            )
                        },
                    )
                elif (
                    call_context.deadline_exceeded()
                    and result.status == ToolStatus.SUCCEEDED
                ):
                    result = ToolResult.interrupted(
                        "工具超过有效截止时间后才返回，结果不再用于本次运行。",
                        error_code="tool_deadline_exceeded",
                        error_category=ToolErrorCategory.TIMEOUT,
                        metadata={"underlying_operation_completed": True},
                    )
            tool_node.set_output(
                {
                    "status": result.status.value,
                    "runtime_stage": (
                        "validation_failed"
                        if result.error_category
                        in {
                            ToolErrorCategory.VALIDATION,
                            ToolErrorCategory.AUTHORIZATION,
                        }
                        else "executed"
                    ),
                    "error_code": result.error_code,
                    "error_category": (
                        result.error_category.value
                        if result.error_category is not None
                        else None
                    ),
                }
            )
            tool_node.set_output_detail(
                {"tool_result": _tool_result_detail(result)}
            )
            if result.status is not ToolStatus.SUCCEEDED:
                tool_node.set_error(
                    result.error_code,
                    (
                        result.error_category.value
                        if result.error_category is not None
                        else "tool_error"
                    ),
                    result.model_content,
                )
            tool_node.set_status(TraceNodeStatus(result.status.value))
            tool_node.set_attribute(
                "gen_ai.tool.call.result",
                result.status.value,
            )
            if result.error_category is not None:
                tool_node.set_attribute(
                    "gen_ai.tool.error.type",
                    result.error_category.value,
                )
            tool_node.set_attribute(
                "app.domain.retry_count",
                int(result.metadata.get("retry_count") or 0),
            )
            tool_node.set_attribute(
                "app.tool.latency_ms",
                int((perf_counter() - started_at) * 1000),
            )
            return result

    def _finish_tool_call_event(
        self,
        state: AgentRuntimeState,
        step: Any,
        tool_call_row: Any,
        result: ToolResult[Any],
        result_summary: dict[str, Any],
    ) -> RenderEvent:
        with self._recorder.node(
            TraceNodeSpec(
                run_id=state.require_run_id(),
                node_key=(
                    f"tool_call_finished:{step.id}:{tool_call_row.tool_call_id}"
                ),
                node_type=TraceNodeType.PERSISTENCE,
                name="persist_tool_call_result",
                display_name=f"持久化工具结果：{tool_call_row.tool_name}",
                attributes=tool_attributes(
                    tool_name=tool_call_row.tool_name,
                    run_id=state.require_run_id(),
                    step_id=step.id,
                    tool_call_id=tool_call_row.tool_call_id,
                ),
            ),
            input_data={
                "tool_name": tool_call_row.tool_name,
                "tool_call_id": tool_call_row.tool_call_id,
                "result_status": result.status.value,
            },
            input_detail={"result_summary": result_summary},
        ) as persistence_node:
            status = AgentToolCallStatus(result.status.value)
            agent_run_repository.finish_tool_call(
                self._session,
                tool_call_row,
                status=status,
                result_summary=result_summary,
                error_code=result.error_code,
            )
            event_type = (
                "tool-result"
                if result.status == ToolStatus.SUCCEEDED
                else "tool-failed"
            )
            persistence_node.set_output(
                {
                    "status": status.value,
                    "event_type": event_type,
                    "error_code": result.error_code,
                }
            )
            return self._publish(
                state,
                event_type,
                {
                    "record_id": state.record.id,
                    "tool_call_id": tool_call_row.tool_call_id,
                    "step_id": step.id,
                    "tool_name": tool_call_row.tool_name,
                    "status": status.value,
                    "latency_ms": tool_call_row.latency_ms,
                    "result_summary": result_summary,
                    **result_summary,
                },
                step.id,
            )

    def _interrupt_open_tool_calls(
        self,
        state: AgentRuntimeState,
        step: Any,
        calls: list[ToolCall],
        tool_call_rows: dict[str, Any],
        *,
        reason: str,
    ) -> Generator[RenderEvent, None, None]:
        """按模型调用顺序关闭尚未产生结果的 Tool Call。"""

        for call in calls:
            row = tool_call_rows.pop(call.call_id, None)
            if row is None:
                continue
            result = ToolResult.interrupted(
                reason,
                error_code="tool_call_interrupted",
            )
            yield self._finish_tool_call_event(
                state,
                step,
                row,
                result,
                {
                    "success": False,
                    "status": result.status.value,
                    "error_code": result.error_code,
                },
            )

    def _publish(
        self,
        state: AgentRuntimeState,
        event_type: str,
        payload: dict[str, Any],
        step_id: int | None = None,
    ) -> RenderEvent:
        event = self._event_publisher.publish(
            state.require_run_id(),
            event_type,
            payload,
            step_id=step_id,
        )
        self._session.commit()
        return event


def _args_summary(args: dict[str, Any]) -> dict[str, Any]:
    return _bounded_summary(args)


_MAX_SUMMARY_CHARS = 2000


def _bounded_summary(value: dict[str, Any]) -> dict[str, Any]:
    """递归脱敏并限制摘要长度；超限时按结构裁剪而不是截断成不可解析内容。"""

    sanitized = _redact_sensitive(value)
    encoded = orjson.dumps(sanitized).decode()
    if len(encoded) <= _MAX_SUMMARY_CHARS:
        return cast(dict[str, Any], sanitized)
    # 逐级收紧（保留的列表项数 / 单个字符串上限），保持 schema、统计和前 N 行可读。
    for list_keep, string_cap in ((6, 240), (3, 120), (1, 60), (0, 30)):
        pruned = _prune_summary(sanitized, list_keep=list_keep, string_cap=string_cap)
        pruned["_truncated"] = True
        if len(orjson.dumps(pruned).decode()) <= _MAX_SUMMARY_CHARS:
            return pruned
    # 结构裁剪仍超限（键名过多等）时保留最小可诊断信息。
    return {"_truncated": True, "keys": sorted(str(key) for key in sanitized)}


def _prune_summary(value: Any, *, list_keep: int, string_cap: int) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _prune_summary(item, list_keep=list_keep, string_cap=string_cap)
            for key, item in value.items()
        }
    if isinstance(value, list):
        pruned_items = [
            _prune_summary(item, list_keep=list_keep, string_cap=string_cap)
            for item in value[:list_keep]
        ]
        if len(value) > list_keep:
            pruned_items.append({"_truncated_count": len(value) - list_keep})
        return pruned_items
    if isinstance(value, str) and len(value) > string_cap:
        return value[:string_cap]
    return value


def _redact_sensitive(value: Any, key: str = "") -> Any:
    normalized_key = key.lower().replace("-", "_")
    if any(
        marker in normalized_key
        for marker in ("password", "secret", "token", "credential", "api_key")
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _redact_sensitive(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value


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
            "rewrite_question": understanding.get("rewrite_question"),
            "intent": understanding.get("intent") or {},
        }
    )


def _agent_tool_resume_payload(
    state: dict[str, Any],
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    """只为带确定性语义绑定的澄清保存恢复数据。"""

    if not any(
        isinstance(option, dict) and option.get("bindings") for option in options
    ):
        return {}
    scope = state.get("semantic_scope")
    retrieval_id = scope.get("retrieval_id") if isinstance(scope, dict) else None
    return {
        "operation": "resolve_semantic_bindings",
        "retrieval_id": retrieval_id,
        "options": options,
    }


def _result_data(result: ToolResult[Any]) -> dict[str, Any]:
    if result.data is None:
        return {}
    return cast(dict[str, Any], result.data.model_dump(mode="json"))


def _tool_result_detail(result: ToolResult[Any]) -> dict[str, Any]:
    """提取工具结果信封，供脱敏后的 Trace Artifact 保存。"""

    return {
        "status": result.status.value,
        "model_content": result.model_content,
        "data": (
            result.data.model_dump(mode="json") if result.data is not None else None
        ),
        "metadata": result.metadata,
        "error_code": result.error_code,
        "error_category": (
            result.error_category.value
            if result.error_category is not None
            else None
        ),
        "retry_advice": result.retry_advice.value,
        "details": result.details,
    }


def _cancelled_tool_result(result: ToolResult[Any]) -> ToolResult[Any]:
    """取消请求后禁止把已经返回的结果投影为 Agent 业务状态。"""

    if result.status == ToolStatus.INTERRUPTED:
        return result
    return ToolResult.interrupted(
        "用户取消请求已生效，工具结果不再用于本次运行。",
        error_code="tool_cancelled_after_execution",
        metadata={
            "underlying_operation_started": True,
            "underlying_operation_completed": True,
            "original_status": result.status.value,
        },
    )


def _offload_ref(result: ToolResult[Any]) -> str | None:
    value = result.metadata.get("offload_ref")
    return str(value) if value else None


__all__ = [
    "AgentToolExecutor",
    "ToolExecutionResult",
    "ToolExecutionStatus",
]
