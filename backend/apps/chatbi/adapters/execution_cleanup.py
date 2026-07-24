"""ChatBI 联合删除使用的独立事务适配器。"""

from collections.abc import Callable

from sqlmodel import Session

from apps.chatbi.services.conversation.ports import ExecutionCleanupGateway
from apps.chatbi.services.execution import ResultArtifactService
from sqlbot_platform.workflow_engine.run_cleanup import (
    delete_runs,
    list_run_ids_for_chat,
)


class CommittedAgentCleanupGateway:
    """在独立 Session 中执行并提交 Agent 清理。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        cleanup_factory: Callable[[Session], ExecutionCleanupGateway],
    ) -> None:
        self._session_factory = session_factory
        self._cleanup_factory = cleanup_factory

    def delete_for_chat(self, chat_id: int) -> int:
        with self._session_factory() as session:
            try:
                deleted = self._cleanup_factory(session).delete_for_chat(chat_id)
                session.commit()
                return deleted
            except Exception:
                session.rollback()
                raise


class WorkflowRunCleanupGateway:
    """在独立 Session 中查询并清理 Graph 运行数据。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def list_run_ids_for_chat(self, chat_id: int) -> list[str]:
        with self._session_factory() as session:
            return list_run_ids_for_chat(session, chat_id)

    def delete_for_chat(self, chat_id: int) -> int:
        with self._session_factory() as session:
            try:
                run_ids = list_run_ids_for_chat(session, chat_id)
                delete_runs(session, run_ids)
                session.commit()
                return len(run_ids)
            except Exception:
                session.rollback()
                raise


class WorkflowArtifactCleanupGateway:
    """在独立 Session 中登记并处理 Artifact 清理任务。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        service_factory: Callable[[Session], ResultArtifactService],
    ) -> None:
        self._session_factory = session_factory
        self._service_factory = service_factory

    def schedule_chat_cleanup(
        self,
        chat_id: int,
        *,
        legacy_execution_ids: list[str],
    ) -> int:
        with self._session_factory() as session:
            try:
                scheduled = self._service_factory(session).schedule_chat_cleanup(
                    chat_id,
                    legacy_execution_ids=legacy_execution_ids,
                )
                session.commit()
                return scheduled
            except Exception:
                session.rollback()
                raise

    def process_pending_cleanup(self) -> int:
        with self._session_factory() as session:
            try:
                processed = self._service_factory(session).process_pending_cleanup()
                session.commit()
                return processed
            except Exception:
                session.rollback()
                raise


__all__ = [
    "CommittedAgentCleanupGateway",
    "WorkflowArtifactCleanupGateway",
    "WorkflowRunCleanupGateway",
]
