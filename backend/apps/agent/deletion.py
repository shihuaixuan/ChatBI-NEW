"""Agent 执行数据的公开删除入口。"""

from sqlalchemy import delete
from sqlmodel import Session, col, select

from apps.agent.models import (
    ChatbiAgentClarification,
    ChatbiAgentRun,
    ChatbiAgentStep,
    ChatbiAgentTraceEvent,
)


class AgentExecutionDeletionService:
    """按会话删除 Agent run、步骤、事件和澄清记录。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def delete_for_chat(self, chat_id: int) -> int:
        run_ids = list(
            self._session.exec(
                select(ChatbiAgentRun.id).where(ChatbiAgentRun.chat_id == chat_id)
            ).all()
        )
        if not run_ids:
            return 0
        self._session.execute(
            delete(ChatbiAgentTraceEvent).where(
                col(ChatbiAgentTraceEvent.run_id).in_(run_ids)
            )
        )
        self._session.execute(
            delete(ChatbiAgentClarification).where(
                col(ChatbiAgentClarification.run_id).in_(run_ids)
            )
        )
        self._session.execute(
            delete(ChatbiAgentStep).where(col(ChatbiAgentStep.run_id).in_(run_ids))
        )
        self._session.execute(
            delete(ChatbiAgentRun).where(col(ChatbiAgentRun.id).in_(run_ids))
        )
        return len(run_ids)


__all__ = ["AgentExecutionDeletionService"]
