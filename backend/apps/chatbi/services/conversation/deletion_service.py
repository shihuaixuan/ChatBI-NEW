"""会话及其执行数据的同生命周期删除（原 apps/chat/services/deletion.py，R3-c2 迁入）。

Graph 运行数据经 Workflow Engine 公开清理入口删除；Agent 执行数据经注入的清理端口
删除（Agent 归位 chatbi/orchestration 前不直接导入执行器目录）。
"""

from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, col, select

from apps.chatbi.models import Chat, ChatLog, ChatRecord
from apps.chatbi.services.conversation.ports import ExecutionCleanupGateway
from apps.chatbi.services.execution.result_artifacts import ResultArtifactService
from apps.workflow_engine.run_cleanup import delete_runs, list_run_ids_for_chat


class ChatDeletionService:
    """删除用户会话、历史快照及关联的 Agent、Graph 执行数据。"""

    def __init__(
        self,
        session: Session,
        *,
        agent_cleanup: ExecutionCleanupGateway,
        result_artifact_service: ResultArtifactService,
    ) -> None:
        self._session = session
        self._agent_cleanup = agent_cleanup
        self._result_artifact_service = result_artifact_service

    def delete_for_user(self, current_user: Any, chat_id: int) -> str:
        chat = self._session.get(Chat, chat_id)
        if chat is None:
            return f"Chat with id {chat_id} has been deleted"
        if chat.create_by != current_user.id:
            raise ValueError(f"Chat with id {chat_id} not Owned by the current user")

        record_ids = list(
            self._session.exec(select(ChatRecord.id).where(ChatRecord.chat_id == chat_id)).all()
        )
        run_ids = list_run_ids_for_chat(self._session, chat_id)
        # 先在同一事务登记正文清理任务并删除元数据，提交成功后再处理正文。
        self._result_artifact_service.schedule_chat_cleanup(
            chat_id,
            legacy_execution_ids=run_ids,
        )

        delete_runs(self._session, run_ids)
        self._agent_cleanup.delete_for_chat(chat_id)
        if record_ids:
            self._session.execute(
                delete(ChatLog).where(col(ChatLog.pid).in_(record_ids))
            )
            self._session.execute(
                delete(ChatRecord).where(col(ChatRecord.id).in_(record_ids))
            )
        self._session.delete(chat)
        self._session.commit()

        # 元数据删除成功后处理正文；失败状态会保留，供启动流程继续重试。
        self._result_artifact_service.process_pending_cleanup()
        return f"Chat with id {chat_id} has been deleted"


__all__ = ["ChatDeletionService"]
