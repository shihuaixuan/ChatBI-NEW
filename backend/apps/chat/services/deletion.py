"""集中处理 Chat 与其 Graph Workflow 数据的同生命周期删除。"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, select

from apps.chatbi.models import Chat, ChatLog, ChatRecord
from apps.workflow_engine.infrastructure.artifacts.cleanup import ArtifactCleanupService
from apps.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
    NodeExecutionModel,
    WorkflowArtifactCleanupModel,
    WorkflowArtifactModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
    WorkflowRunModel,
)


class ChatDeletionService:
    """删除用户会话、历史快照和物理关联的 Graph 执行数据。"""

    def __init__(self, session: Session) -> None:
        self._session = session

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
        artifacts = (
            self._session.exec(
                select(WorkflowArtifactModel).where(WorkflowArtifactModel.run_id.in_(run_ids))
            ).all()
            if run_ids
            else []
        )

        now = datetime.now(timezone.utc)
        artifact_ids = [artifact.artifact_id for artifact in artifacts]
        existing_cleanup_ids = (
            set(
                self._session.exec(
                    select(WorkflowArtifactCleanupModel.artifact_id).where(
                        WorkflowArtifactCleanupModel.artifact_id.in_(artifact_ids)
                    )
                ).all()
            )
            if artifact_ids
            else set()
        )
        for artifact in artifacts:
            if artifact.artifact_id in existing_cleanup_ids:
                continue
            self._session.add(
                WorkflowArtifactCleanupModel(
                    artifact_id=artifact.artifact_id,
                    storage_uri=artifact.storage_uri,
                    status="pending",
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )

        if run_ids:
            self._session.execute(delete(NodeExecutionModel).where(NodeExecutionModel.run_id.in_(run_ids)))
            self._session.execute(
                delete(WorkflowCheckpointModel).where(WorkflowCheckpointModel.run_id.in_(run_ids))
            )
            self._session.execute(delete(WorkflowEventModel).where(WorkflowEventModel.run_id.in_(run_ids)))
            self._session.execute(
                delete(InteractionRequestModel).where(InteractionRequestModel.run_id.in_(run_ids))
            )
            self._session.execute(delete(WorkflowArtifactModel).where(WorkflowArtifactModel.run_id.in_(run_ids)))
            self._session.execute(delete(WorkflowRunModel).where(WorkflowRunModel.run_id.in_(run_ids)))
        if record_ids:
            self._session.execute(delete(ChatLog).where(ChatLog.pid.in_(record_ids)))
            self._session.execute(delete(ChatRecord).where(ChatRecord.id.in_(record_ids)))
        self._session.delete(chat)
        self._session.commit()

        # 元数据删除成功后处理正文；失败状态会保留，供启动流程继续重试。
        ArtifactCleanupService(self._session).process_pending()
        return f"Chat with id {chat_id} has been deleted"
