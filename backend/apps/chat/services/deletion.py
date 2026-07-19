"""集中处理 Chat 与 Agent、Graph 执行数据的同生命周期删除。"""

from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, col, select

from apps.agent.deletion import AgentExecutionDeletionService
from apps.chatbi.models import Chat, ChatLog, ChatRecord
from apps.chatbi.services import ResultArtifactService
from apps.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
    NodeExecutionModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
    WorkflowRunModel,
)
from infrastructure.result_artifacts import build_workflow_artifact_gateway


class ChatDeletionService:
    """删除用户会话、历史快照及关联的 Agent、Graph 执行数据。"""

    def __init__(
        self,
        session: Session,
        *,
        result_artifact_service: ResultArtifactService | None = None,
    ) -> None:
        self._session = session
        self._result_artifact_service = (
            result_artifact_service
            or ResultArtifactService(build_workflow_artifact_gateway(session))
        )

    def delete_for_user(self, current_user: Any, chat_id: int) -> str:
        chat = self._session.get(Chat, chat_id)
        if chat is None:
            return f"Chat with id {chat_id} has been deleted"
        if chat.create_by != current_user.id:
            raise ValueError(f"Chat with id {chat_id} not Owned by the current user")

        record_ids = list(
            self._session.exec(select(ChatRecord.id).where(ChatRecord.chat_id == chat_id)).all()
        )
        run_ids = list(
            self._session.exec(
                select(WorkflowRunModel.run_id).where(WorkflowRunModel.chat_id == chat_id)
            ).all()
        )
        # 先在同一事务登记正文清理任务并删除元数据，提交成功后再处理正文。
        self._result_artifact_service.schedule_chat_cleanup(
            chat_id,
            legacy_execution_ids=run_ids,
        )

        if run_ids:
            self._session.execute(
                delete(NodeExecutionModel).where(
                    col(NodeExecutionModel.run_id).in_(run_ids)
                )
            )
            self._session.execute(
                delete(WorkflowCheckpointModel).where(
                    col(WorkflowCheckpointModel.run_id).in_(run_ids)
                )
            )
            self._session.execute(
                delete(WorkflowEventModel).where(
                    col(WorkflowEventModel.run_id).in_(run_ids)
                )
            )
            self._session.execute(
                delete(InteractionRequestModel).where(
                    col(InteractionRequestModel.run_id).in_(run_ids)
                )
            )
            self._session.execute(
                delete(WorkflowRunModel).where(
                    col(WorkflowRunModel.run_id).in_(run_ids)
                )
            )
        AgentExecutionDeletionService(self._session).delete_for_chat(chat_id)
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
