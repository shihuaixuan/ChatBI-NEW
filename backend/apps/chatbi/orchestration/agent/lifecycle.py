"""Agent Run、ChatRecord 和 Clarification 的统一生命周期。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import orjson

from apps.chatbi.models import (
    AgentClarificationResumeKind,
    AgentRunStatus,
    AgentToolCallStatus,
)
from apps.chatbi.orchestration.agent.messages import close_unfinished_tool_calls
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.conversation import (
    ChatRecordExecutionType,
    ChatRecordResultProjection,
    ChatRecordService,
    ChatRecordStatus,
)
from apps.event import EventPublisher, RenderEvent
from apps.memory import MemoryService, SuccessfulQueryMemoryEvent
from apps.trace import (
    AgentTraceRecorder,
    TraceNodeHandle,
    TraceNodeSpec,
    TraceNodeStatus,
    TraceNodeType,
)


class AgentLifecycle:
    """集中维护 Agent 与问数记录的一致状态迁移。"""

    def __init__(
        self,
        session: Any,
        current_user_id: int | None,
        record_service: ChatRecordService,
        event_publisher: EventPublisher,
        trace_recorder: AgentTraceRecorder,
        memory_service: MemoryService | None = None,
    ) -> None:
        self._session = session
        self._current_user_id = current_user_id
        self._record_service = record_service
        self._event_publisher = event_publisher
        self._trace_recorder = trace_recorder
        self._memory_service = memory_service

    def start(self, state: AgentRuntimeState) -> Iterator[RenderEvent]:
        """把新 Run 和 ChatRecord 一起置为运行态。"""

        run = state.run
        record = state.record
        self._session.refresh(run)
        if run.status == AgentRunStatus.CANCELLED.value:
            return
        if run.status == AgentRunStatus.CANCEL_REQUESTED.value:
            yield from self.finalize_cancellation(
                state,
                "用户在 Agent 开始前请求取消运行",
                stage="before_execution",
            )
            return
        with self._transition_node(
            state,
            "persist_run_started",
            "持久化运行开始状态",
            input_data={"run_status": run.status, "record_status": record.status},
        ) as node:
            agent_run_repository.update_run(
                self._session,
                run,
                status=AgentRunStatus.RUNNING.value,
            )
            self._record_service.transition(
                record,
                ChatRecordStatus.RUNNING,
                execution_type=ChatRecordExecutionType.AGENT,
            )
            self._session.commit()
            node.set_output(
                {"run_status": run.status, "record_status": record.status}
            )
            yield self._publish(
                state,
                "record-created",
                {"record_id": record.id, "id": record.id, "run_id": run.id},
            )
            yield self._publish(
                state,
                "run-started",
                {"record_id": record.id, "run_id": run.id},
            )

    def resume(self, state: AgentRuntimeState) -> RenderEvent:
        """保存恢复后的上下文，并把 Run 和 ChatRecord 一起恢复为运行态。"""

        run = state.run
        record = state.record
        with self._transition_node(
            state,
            "persist_run_resumed",
            "持久化澄清恢复状态",
            input_data={"run_status": run.status, "record_status": record.status},
        ) as node:
            agent_run_repository.update_run(
                self._session,
                run,
                status=AgentRunStatus.RUNNING.value,
                messages=state.serialized_messages(),
                budget_snapshot=state.budget_snapshot(),
                derived_state=state.persistable_context(),
            )
            self._record_service.transition(
                record,
                ChatRecordStatus.RUNNING,
                execution_type=ChatRecordExecutionType.AGENT,
            )
            self._session.commit()
            node.set_output(
                {
                    "run_status": run.status,
                    "record_status": record.status,
                    "message_count": len(state.messages),
                }
            )
            return self._publish(
                state,
                "clarification-accepted",
                {"record_id": record.id, "run_id": run.id},
            )

    def suspend(
        self,
        state: AgentRuntimeState,
        question: str,
        options: list[dict[str, Any]],
        call_id: str | None,
        step_id: int | None,
        *,
        resume_kind: AgentClarificationResumeKind,
        resume_payload: dict[str, Any],
    ) -> RenderEvent:
        """创建澄清记录，并把 Run 和 ChatRecord 一起置为等待用户。"""

        run = state.run
        record = state.record
        exclude = {call_id} if call_id else set()
        close_unfinished_tool_calls(
            state.messages,
            content="skipped: run suspended for clarification before this tool executed",
            exclude_ids=exclude,
        )
        with self._transition_node(
            state,
            "persist_run_suspended",
            "持久化澄清等待状态",
            node_type=TraceNodeType.INTERACTION,
            input_data={
                "resume_kind": resume_kind.value,
                "tool_call_id": call_id,
                "step_id": step_id,
                "option_count": len(options),
            },
            input_detail={
                "question": question,
                "options": options,
                "resume_payload": resume_payload,
            },
        ) as node:
            clarification = agent_run_repository.create_clarification(
                self._session,
                run,
                question=question,
                options=options,
                tool_call_id=call_id,
                resume_kind=resume_kind.value,
                resume_payload=resume_payload,
                user_id=self._current_user_id,
            )
            self._record_service.transition(
                record,
                ChatRecordStatus.WAITING_USER,
                execution_type=ChatRecordExecutionType.AGENT,
            )
            agent_run_repository.update_run(
                self._session,
                run,
                status=AgentRunStatus.WAITING_USER.value,
                messages=state.serialized_messages(),
                budget_snapshot=state.budget_snapshot(),
                derived_state=state.persistable_context(),
            )
            self._session.commit()
            node.set_status(TraceNodeStatus.WAITING)
            node.set_output(
                {
                    "clarification_id": clarification.id,
                    "run_status": run.status,
                    "record_status": record.status,
                }
            )
            return self._publish(
                state,
                "clarification",
                {
                    "record_id": record.id,
                    "clarification_id": clarification.id,
                    "tool_call_id": call_id,
                    "step_id": step_id,
                    "question": clarification.question,
                    "options": clarification.options or [],
                },
                step_id,
            )

    def _record_successful_query_memory(self, state: AgentRuntimeState) -> None:
        """只把成功问数的结构化查询形态交给记忆模块。"""

        if self._memory_service is None or state.context.user_id is None:
            return
        understanding = state.context.state.get("question_understanding")
        if not isinstance(understanding, dict):
            return
        intent = understanding.get("intent")
        if not isinstance(intent, dict):
            return
        intent_type = intent.get("intent_type")
        query_shape = intent.get("query_shape")
        if not isinstance(intent_type, str) or not isinstance(query_shape, dict):
            return
        run_id = state.run.id
        if run_id is None:
            return
        self._memory_service.record_successful_query(
            state.run.oid,
            state.context.user_id,
            SuccessfulQueryMemoryEvent(
                session_id=str(state.run.chat_id),
                run_id=str(run_id),
                intent_type=intent_type,
                query_shape=query_shape,
            ),
        )

    def finish(
        self,
        state: AgentRuntimeState,
        *,
        answer: str,
        chart: dict[str, Any],
        sql: str | None,
        step_id: int | None = None,
        full_data: Any = None,
        execution: dict[str, Any] | None = None,
        claims: list[dict[str, Any]] | None = None,
        caliber_card: dict[str, Any] | None = None,
        chart_spec: dict[str, Any] | None = None,
    ) -> Iterator[RenderEvent]:
        """保存最终结果，并把 Run 和 ChatRecord 一起置为成功。"""

        run = state.run
        record = state.record
        close_unfinished_tool_calls(state.messages)
        with self._transition_node(
            state,
            "persist_run_finished",
            "持久化最终回答",
            input_data={
                "step_id": step_id,
                "has_execution": execution is not None,
                "has_full_data": isinstance(full_data, list),
            },
            input_detail={"answer": answer, "chart": chart, "sql": sql},
        ) as node:
            record_data = None
            if full_data is not None and execution:
                record_payload = {
                    "fields": execution.get("fields") or [],
                    "data": full_data,
                }
                if execution.get("artifact_ref") is not None:
                    record_payload["artifact_ref"] = execution["artifact_ref"]
                record_data = orjson.dumps(record_payload).decode()
            self._record_service.transition(
                record,
                ChatRecordStatus.SUCCEEDED,
                execution_type=ChatRecordExecutionType.AGENT,
                result=ChatRecordResultProjection(
                    answer=answer,
                    chart_answer=answer,
                    sql=sql,
                    chart=orjson.dumps(chart or {}).decode(),
                    data=record_data,
                ),
            )
            agent_run_repository.update_run(
                self._session,
                run,
                status=AgentRunStatus.FINISHED.value,
                messages=state.serialized_messages(),
                budget_snapshot=state.budget_snapshot(),
            )
            self._session.commit()
            self._record_successful_query_memory(state)
            node.set_output(
                {
                    "run_status": run.status,
                    "record_status": record.status,
                    "answer_length": len(answer),
                    "has_sql": bool(sql),
                }
            )
            node.set_output_detail(
                {"final_answer": answer, "sql": sql, "execution": execution or {}}
            )
            yield self._publish(
                state,
                "answer",
                {
                    "record_id": record.id,
                    "content": answer,
                    "claims": list(claims or []),
                    "caliber_card": dict(caliber_card or {}),
                    "chart_spec": dict(chart_spec or chart or {}),
                },
                step_id,
            )
            yield self._publish(
                state,
                "run-finished",
                {
                    "record_id": record.id,
                    "content": answer,
                    "answer": answer,
                    "chart": chart,
                    "claims": list(claims or []),
                    "caliber_card": dict(caliber_card or {}),
                    "chart_spec": dict(chart_spec or chart or {}),
                },
                step_id,
            )

    def fail(
        self,
        state: AgentRuntimeState,
        message: str,
        error_class: str,
        error_details: dict[str, Any] | None = None,
    ) -> Iterator[RenderEvent]:
        """保存错误，并把 Run 和 ChatRecord 一起置为失败。"""

        # 前序数据库操作失败后 PostgreSQL 会拒绝当前事务内的所有后续语句；
        # 先回滚，再用新事务持久化统一的失败终态。
        self._session.rollback()
        run = state.run
        record = state.record
        with self._transition_node(
            state,
            "persist_run_failed",
            "持久化运行失败状态",
            input_data={"error_class": error_class},
            input_detail={
                "error": message,
                **({"error_details": error_details} if error_details else {}),
            },
        ) as node:
            close_unfinished_tool_calls(
                state.messages,
                content="skipped: run failed before this tool executed",
            )
            self._record_service.transition(
                record,
                ChatRecordStatus.FAILED,
                error=message,
                execution_type=ChatRecordExecutionType.AGENT,
            )
            agent_run_repository.update_run(
                self._session,
                run,
                status=AgentRunStatus.FAILED.value,
                messages=state.serialized_messages(),
                budget_snapshot=state.budget_snapshot(),
                error_class=error_class,
                error=message,
            )
            self._session.commit()
            node.set_error(error_class, "agent_run", message)
            node.set_output(
                {"run_status": run.status, "record_status": record.status}
            )
            yield self._publish(
                state,
                "run-failed",
                {
                    "record_id": record.id,
                    "content": message,
                    "error_class": error_class,
                    **({"error_details": error_details} if error_details else {}),
                },
            )

    def finalize_cancellation(
        self,
        state: AgentRuntimeState,
        message: str = "用户已请求取消运行",
        *,
        stage: str | None = None,
    ) -> Iterator[RenderEvent]:
        """统一收口 Run、Step、Tool Call、ChatRecord 和取消事件。"""

        # 取消收口必须使用干净事务，避免前序工具异常阻塞后续状态写入。
        self._session.rollback()
        run = state.run
        record = state.record
        self._session.refresh(run)
        if run.status == AgentRunStatus.CANCELLED.value:
            return
        if run.status in {
            AgentRunStatus.FINISHED.value,
            AgentRunStatus.FAILED.value,
        }:
            return
        if run.status != AgentRunStatus.CANCEL_REQUESTED.value:
            raise ValueError(f"AGENT_CANCEL_STATE_CONFLICT:{run.status}")
        effective_stage = stage or run.cancel_stage or "unknown"
        with self._transition_node(
            state,
            "persist_run_cancelled",
            "持久化运行取消状态",
            input_data={"stage": effective_stage},
            input_detail={"reason": message},
        ) as node:
            running_step = agent_run_repository.get_running_step(
                self._session,
                state.require_run_id(),
            )
            if running_step is not None:
                agent_run_repository.cancel_step(
                    self._session,
                    running_step,
                    message,
                )
            for tool_call in agent_run_repository.list_running_tool_calls(
                self._session,
                state.require_run_id(),
            ):
                agent_run_repository.finish_tool_call(
                    self._session,
                    tool_call,
                    status=AgentToolCallStatus.INTERRUPTED,
                    result_summary={
                        "success": False,
                        "status": AgentToolCallStatus.INTERRUPTED.value,
                        "error_code": "tool_call_interrupted",
                        "cancel_stage": effective_stage,
                    },
                    error_code="tool_call_interrupted",
                )
            close_unfinished_tool_calls(
                state.messages,
                content="skipped: run cancelled before this tool executed",
            )
            self._record_service.transition(
                record,
                ChatRecordStatus.CANCELLED,
                error=message,
                execution_type=ChatRecordExecutionType.AGENT,
            )
            agent_run_repository.mark_cancelled(
                self._session,
                run,
                stage=effective_stage,
                reason=message,
            )
            agent_run_repository.update_run(
                self._session,
                run,
                messages=state.serialized_messages(),
                budget_snapshot=state.budget_snapshot(),
                derived_state=state.persistable_context(),
                error=message,
            )
            self._session.commit()
            node.set_status(TraceNodeStatus.CANCELLED)
            node.set_output(
                {"run_status": run.status, "record_status": record.status}
            )
            yield self._publish(
                state,
                "run-cancelled",
                {
                    "record_id": record.id,
                    "content": message,
                    "cancel_stage": effective_stage,
                    "cancel_requested_at": (
                        run.cancel_requested_at.isoformat()
                        if run.cancel_requested_at is not None
                        else None
                    ),
                    "cancelled_at": (
                        run.cancelled_at.isoformat()
                        if run.cancelled_at is not None
                        else None
                    ),
                },
            )

    def cancel(
        self,
        state: AgentRuntimeState,
        message: str = "用户已请求取消运行",
    ) -> Iterator[RenderEvent]:
        """兼容旧调用方，统一转入取消终态收口。"""

        yield from self.finalize_cancellation(state, message)

    @contextmanager
    def _transition_node(
        self,
        state: AgentRuntimeState,
        name: str,
        display_name: str,
        *,
        node_type: TraceNodeType = TraceNodeType.PERSISTENCE,
        input_data: dict[str, Any] | None = None,
        input_detail: dict[str, Any] | None = None,
    ) -> Iterator[TraceNodeHandle]:
        """统一记录 Run 与问数记录的状态迁移。"""

        with self._trace_recorder.node(
            TraceNodeSpec(
                run_id=state.require_run_id(),
                node_type=node_type,
                name=name,
                display_name=display_name,
            ),
            input_data=input_data,
            input_detail=input_detail,
        ) as node:
            yield node

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


__all__ = ["AgentLifecycle"]
