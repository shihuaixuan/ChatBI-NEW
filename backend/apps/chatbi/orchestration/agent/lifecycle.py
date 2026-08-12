"""Agent Run、ChatRecord 和 Clarification 的统一生命周期。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import orjson

from apps.chatbi.models import (
    AgentClarificationResumeKind,
    AgentRunStatus,
    QueryFinalReplyProjectionData,
)
from apps.chatbi.orchestration.agent.messages import close_unfinished_tool_calls
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.generation.final_reply import project_query_final_reply
from apps.conversation import (
    ChatRecordExecutionType,
    ChatRecordResultProjection,
    ChatRecordService,
    ChatRecordStatus,
)
from apps.event import EventPublisher, RenderEvent


class AgentLifecycle:
    """集中维护 Agent 与问数记录的一致状态迁移。"""

    def __init__(
        self,
        session: Any,
        current_user_id: int | None,
        record_service: ChatRecordService,
        event_publisher: EventPublisher,
    ) -> None:
        self._session = session
        self._current_user_id = current_user_id
        self._record_service = record_service
        self._event_publisher = event_publisher

    def start(self, state: AgentRuntimeState) -> Iterator[RenderEvent]:
        """把新 Run 和 ChatRecord 一起置为运行态。"""

        run = state.run
        record = state.record
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
    ) -> Iterator[RenderEvent]:
        """保存最终结果，并把 Run 和 ChatRecord 一起置为成功。"""

        run = state.run
        record = state.record
        close_unfinished_tool_calls(state.messages)
        if execution is not None and isinstance(full_data, list):
            budget_notice = answer.startswith("预算已达上限")
            understanding = state.context.state.get("question_understanding")
            intent = (
                understanding.get("intent")
                if isinstance(understanding, dict)
                and isinstance(understanding.get("intent"), dict)
                else {}
            )
            grounded = project_query_final_reply(
                QueryFinalReplyProjectionData(
                    answer_markdown=answer,
                    execution=execution,
                    rows=full_data,
                    intent=intent,
                )
            )
            answer = (
                f"预算已达上限，以下仅展示已成功执行的查询结果。\n\n{grounded.answer}"
                if budget_notice
                else grounded.answer
            )
            sql = grounded.sql
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
        yield self._publish(
            state,
            "answer",
            {"record_id": record.id, "content": answer},
            step_id,
        )
        yield self._publish(
            state,
            "run-finished",
            {"record_id": record.id, "content": answer},
            step_id,
        )

    def fail(
        self,
        state: AgentRuntimeState,
        message: str,
        error_class: str,
    ) -> Iterator[RenderEvent]:
        """保存错误，并把 Run 和 ChatRecord 一起置为失败。"""

        run = state.run
        record = state.record
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
        yield self._publish(
            state,
            "run-failed",
            {
                "record_id": record.id,
                "content": message,
                "error_class": error_class,
            },
        )

    def cancel(
        self,
        state: AgentRuntimeState,
        message: str = "用户已请求取消运行",
    ) -> Iterator[RenderEvent]:
        """在当前执行边界确认停止后，才把 Run 和问数记录置为已取消。"""

        run = state.run
        record = state.record
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
        agent_run_repository.update_run(
            self._session,
            run,
            status=AgentRunStatus.CANCELLED.value,
            messages=state.serialized_messages(),
            budget_snapshot=state.budget_snapshot(),
            error=message,
        )
        self._session.commit()
        yield self._publish(
            state,
            "run-cancelled",
            {"record_id": record.id, "content": message},
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


__all__ = ["AgentLifecycle"]
