"""Workflow Engine Artifact 的公开存储与清理实现。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import orjson
from sqlalchemy import delete, or_
from sqlmodel import Session, col, select

from common.core.db import engine
from sqlbot_platform.workflow_engine.domain.artifact import ArtifactRef
from sqlbot_platform.workflow_engine.infrastructure.artifacts.cleanup import (
    ArtifactCleanupService,
)
from sqlbot_platform.workflow_engine.infrastructure.artifacts.file_store import (
    FileArtifactStore,
    SessionArtifactMetadataStore,
    workflow_artifact_root,
)
from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    WorkflowArtifactCleanupModel,
    WorkflowArtifactModel,
)


class WorkflowArtifactGateway:
    """提供通用 JSON Artifact 写入和按元数据登记清理任务。"""

    def __init__(
        self,
        session: Session,
        *,
        session_factory: Callable[[], Session],
    ) -> None:
        self._session = session
        self._store = FileArtifactStore(
            root=workflow_artifact_root(),
            metadata_store=SessionArtifactMetadataStore(session_factory),
        )

    def put_json(
        self,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        return self._store.put_json(
            run_id=run_id,
            kind=kind,
            payload=payload,
            metadata=metadata,
        )

    def get_json(self, artifact_id: str) -> dict[str, Any]:
        """读取并校验一个 JSON Artifact，正文完整性由 Store 统一保证。"""

        artifact, content = self._store.get(artifact_id)
        if artifact.content_type != "application/json":
            raise ValueError("ARTIFACT_CONTENT_TYPE_INVALID")
        payload = orjson.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("ARTIFACT_JSON_OBJECT_REQUIRED")
        return {
            **artifact.model_dump(mode="json"),
            "payload": payload,
        }

    def schedule_cleanup(
        self,
        *,
        metadata: dict[str, str | int],
        execution_ids: list[str] | None = None,
    ) -> int:
        """登记匹配 Artifact 的正文清理任务，并在当前事务删除元数据。"""

        criteria = []
        if metadata:
            criteria.append(col(WorkflowArtifactModel.metadata_json).contains(metadata))
        if execution_ids:
            criteria.append(col(WorkflowArtifactModel.run_id).in_(execution_ids))
        if not criteria:
            return 0

        artifacts = self._session.exec(
            select(WorkflowArtifactModel).where(or_(*criteria))
        ).all()
        if not artifacts:
            return 0

        artifact_ids = [artifact.artifact_id for artifact in artifacts]
        existing_cleanup_ids = set(
            self._session.exec(
                select(WorkflowArtifactCleanupModel.artifact_id).where(
                    col(WorkflowArtifactCleanupModel.artifact_id).in_(artifact_ids)
                )
            ).all()
        )
        now = datetime.now(timezone.utc)
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
        self._session.execute(
            delete(WorkflowArtifactModel).where(
                col(WorkflowArtifactModel.artifact_id).in_(artifact_ids)
            )
        )
        return len(artifacts)

    def process_pending_cleanup(self) -> int:
        return ArtifactCleanupService(self._session).process_pending()


def build_workflow_artifact_gateway(session: Session) -> WorkflowArtifactGateway:
    """为业务执行器装配共享 Artifact 存储。"""

    def session_factory() -> Session:
        return Session(engine)

    return WorkflowArtifactGateway(session, session_factory=session_factory)


__all__ = ["WorkflowArtifactGateway", "build_workflow_artifact_gateway"]
