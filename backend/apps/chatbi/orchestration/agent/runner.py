"""Agent 后台执行器与持久化事件流。"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from sqlmodel import Session

from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.models import (
    AgentConfig,
    AgentRunStatus,
    ChatbiAgentClarification,
    ChatbiAgentRun,
)
from apps.chatbi.orchestration.agent.cancellation import (
    DatabaseRunCancellationSignal,
)
from apps.chatbi.orchestration.agent.composition import build_agent_loop
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.conversation import ChatRecordExecutionType, ChatRecordStatus
from apps.event import (
    EventPublisher,
    list_events_after,
)
from apps.event import (
    RenderEvent as ProductRenderEvent,
)
from common.core.db import engine

logger = logging.getLogger(__name__)


class AgentRunner:
    """在独立线程和数据库 Session 中执行 Agent。"""

    def __init__(self, database_engine) -> None:
        self._database_engine = database_engine
        self._executor = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="chatbi-agent-runner",
        )

    def start_initial(self, run_id: int, record_id: int, user_id: int, oid: int) -> None:
        self._executor.submit(
            self._execute,
            run_id,
            record_id,
            user_id,
            oid,
            None,
            None,
        )

    def start_resume(
        self,
        run_id: int,
        record_id: int,
        user_id: int,
        oid: int,
        clarification_id: int,
        answer_text: str,
    ) -> None:
        self._executor.submit(
            self._execute,
            run_id,
            record_id,
            user_id,
            oid,
            clarification_id,
            answer_text,
        )

    def stream(self, run_id: int) -> Iterator[ProductRenderEvent]:
        """从持久化事件表读取事件，SSE 断开不影响后台执行。"""

        sequence = 0
        while True:
            with Session(self._database_engine) as session:
                events = list_events_after(session, run_id, sequence)
                run = agent_run_repository.get_run(session, run_id)
            for event in events:
                sequence = max(sequence, int(event.sequence))
                payload = dict(event.payload or {})
                payload.setdefault("run_id", run_id)
                payload.setdefault("sequence", event.sequence)
                yield ProductRenderEvent.model_validate(payload)
            if run is not None and run.status in {
                AgentRunStatus.FINISHED.value,
                AgentRunStatus.FAILED.value,
                AgentRunStatus.CANCELLED.value,
                AgentRunStatus.WAITING_USER.value,
            } and not events:
                return
            time.sleep(0.05)

    def _execute(
        self,
        run_id: int,
        record_id: int,
        user_id: int,
        oid: int,
        clarification_id: int | None,
        answer_text: str | None,
    ) -> None:
        with Session(self._database_engine) as session:
            run = agent_run_repository.get_run(session, run_id)
            if run is None:
                return
            try:
                record = build_chat_record_service(session).get_owned(
                    user_id,
                    record_id,
                )
                config = AgentConfig.model_validate(run.config)
                current_user = SimpleNamespace(id=user_id, oid=oid)
                loop = build_agent_loop(
                    session,
                    current_user,
                    config,
                    cancellation_signal_factory=lambda current_run_id: (
                        DatabaseRunCancellationSignal(
                            self._database_engine,
                            current_run_id,
                        )
                    ),
                )
                if clarification_id is None:
                    events = loop.run(run, record)
                else:
                    clarification = session.get(
                        ChatbiAgentClarification,
                        clarification_id,
                    )
                    if clarification is None or answer_text is None:
                        raise RuntimeError("AGENT_CLARIFICATION_MISSING")
                    events = loop.resume(run, record, clarification, answer_text)
                for _event in events:
                    pass
            except Exception as exc:
                self._fail_unexpected_run(session, run, record_id, user_id, str(exc))
                logger.exception("Agent Runner 执行失败: run_id=%s", run_id)

    @staticmethod
    def _fail_unexpected_run(
        session,
        run: ChatbiAgentRun,
        record_id: int,
        user_id: int,
        message: str,
    ) -> None:
        session.rollback()
        record = build_chat_record_service(session).get_owned(user_id, record_id)
        build_chat_record_service(session).transition(
            record,
            ChatRecordStatus.FAILED,
            error=message,
            execution_type=ChatRecordExecutionType.AGENT,
        )
        agent_run_repository.update_run(
            session,
            run,
            status=AgentRunStatus.FAILED.value,
            error_class="unexpected_error",
            error=message,
        )
        EventPublisher(session).publish(
            run.id or 0,
            "run-failed",
            {
                "record_id": record.id,
                "run_id": run.id,
                "content": message,
                "error_class": "unexpected_error",
            },
        )
        session.commit()


agent_runner = AgentRunner(engine)


__all__ = ["AgentRunner", "agent_runner"]
