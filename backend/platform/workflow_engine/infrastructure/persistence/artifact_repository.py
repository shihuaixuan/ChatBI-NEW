from sqlmodel import Session, select

from sqlbot_platform.workflow_engine.domain.artifact import WorkflowArtifact
from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    WorkflowArtifactModel,
)


class ArtifactRepository:
    """Artifact 元数据仓储。

    P1 只保存元数据，真实正文可以由本地文件、对象存储或数据库外部系统承载。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def put(self, artifact: WorkflowArtifact) -> WorkflowArtifact:
        model = WorkflowArtifactModel(
            artifact_id=artifact.artifact_id,
            run_id=artifact.run_id,
            kind=artifact.kind,
            content_type=artifact.content_type,
            size=artifact.size,
            digest=artifact.digest,
            storage_uri=artifact.storage_uri,
            metadata_json=artifact.metadata,
            temporary=artifact.temporary,
            created_at=artifact.created_at,
        )
        self._session.add(model)
        self._session.flush()
        return artifact.model_copy(deep=True)

    def get(self, artifact_id: str) -> WorkflowArtifact:
        """按公开 artifact_id 读取完整元数据。"""

        model = self._session.exec(
            select(WorkflowArtifactModel).where(
                WorkflowArtifactModel.artifact_id == artifact_id
            )
        ).one()
        return WorkflowArtifact(
            artifact_id=model.artifact_id,
            run_id=model.run_id,
            kind=model.kind,
            content_type=model.content_type,
            size=model.size,
            digest=model.digest,
            storage_uri=model.storage_uri,
            metadata=model.metadata_json,
            temporary=model.temporary,
            created_at=model.created_at,
        )

    def mark_referenced(self, artifact_id: str) -> None:
        model = self._session.exec(
            select(WorkflowArtifactModel).where(WorkflowArtifactModel.artifact_id == artifact_id)
        ).one()
        model.temporary = False
        self._session.add(model)
