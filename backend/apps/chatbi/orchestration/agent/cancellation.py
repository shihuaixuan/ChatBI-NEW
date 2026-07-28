"""Agent Run 的跨请求取消信号。"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from apps.chatbi.models import AgentRunStatus, ChatbiAgentRun


class DatabaseRunCancellationSignal:
    """每次检查使用独立 Session，避免工具并发共享原运行 Session。"""

    def __init__(self, engine: Any, run_id: int) -> None:
        self._engine = engine
        self._run_id = run_id

    def is_cancelled(self) -> bool:
        with Session(self._engine) as session:
            status = session.exec(
                select(ChatbiAgentRun.status).where(
                    ChatbiAgentRun.id == self._run_id
                )
            ).one_or_none()
        return status in {
            AgentRunStatus.CANCEL_REQUESTED.value,
            AgentRunStatus.CANCELLED.value,
        }


__all__ = ["DatabaseRunCancellationSignal"]
