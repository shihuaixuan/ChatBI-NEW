"""处理可重试的 Artifact 正文清理任务。"""

from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from apps.chatbi.adapters.artifact_store.file_store import delete_artifact_body
from apps.chatbi.models.orm import WorkflowArtifactCleanupModel


class ArtifactCleanupService:
    """显式记录每次正文删除尝试，失败任务可在启动时重试。"""

    def __init__(self, session: Session, root: Path | None = None) -> None:
        self._session = session
        self._root = root

    def process_pending(self, max_attempts: int = 3) -> int:
        tasks = self._session.exec(
            select(WorkflowArtifactCleanupModel).where(
                WorkflowArtifactCleanupModel.status.in_(["pending", "failed"]),
                WorkflowArtifactCleanupModel.attempts < max_attempts,
            )
        ).all()
        processed = 0
        for task in tasks:
            task.attempts += 1
            task.updated_at = datetime.now(timezone.utc)
            try:
                delete_artifact_body(task.storage_uri, root=self._root)
            except (OSError, ValueError) as exc:
                # 只记录可预期的文件或 URI 错误，数据库和编程错误继续向上抛出。
                task.status = "failed"
                task.last_error = str(exc)
            else:
                task.status = "succeeded"
                task.last_error = None
                processed += 1
            self._session.add(task)
        self._session.commit()
        return processed


__all__ = ["ArtifactCleanupService"]
