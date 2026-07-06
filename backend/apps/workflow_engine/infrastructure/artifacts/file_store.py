from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import unquote, urlparse
from uuid import uuid4

from sqlmodel import Session

from apps.workflow_engine.domain.artifact import ArtifactRef, WorkflowArtifact
from apps.workflow_engine.infrastructure.persistence.artifact_repository import (
    ArtifactRepository,
)


class ArtifactMetadataStore(Protocol):
    """Artifact 元数据存储协议。"""

    def put(self, artifact: WorkflowArtifact) -> WorkflowArtifact: ...

    def get(self, artifact_id: str) -> WorkflowArtifact: ...


class SessionArtifactMetadataStore:
    """每次操作创建独立 Session，允许并行查询安全写入元数据。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def put(self, artifact: WorkflowArtifact) -> WorkflowArtifact:
        with self._session_factory() as session:
            stored = ArtifactRepository(session).put(artifact)
            session.commit()
            return stored

    def get(self, artifact_id: str) -> WorkflowArtifact:
        with self._session_factory() as session:
            return ArtifactRepository(session).get(artifact_id)


class FileArtifactStore:
    """正文落本地文件、元数据落仓储的 Artifact Store。"""

    def __init__(self, root: str | Path, metadata_store: ArtifactMetadataStore) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._metadata_store = metadata_store

    def put_json(
        self,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        content = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        artifact_id = f"artifact-{uuid4().hex}"
        final_path = self._root / f"{artifact_id}.json"
        temporary_path = self._root / f".{artifact_id}.tmp"
        temporary_path.write_bytes(content)
        temporary_path.replace(final_path)
        artifact = WorkflowArtifact(
            artifact_id=artifact_id,
            run_id=run_id,
            kind=kind,
            content_type="application/json",
            size=len(content),
            digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
            metadata=metadata or {},
            storage_uri=final_path.as_uri(),
            created_at=datetime.now(timezone.utc),
        )
        try:
            self._metadata_store.put(artifact)
        except Exception:
            # 元数据失败时不能留下无法引用的正文。
            final_path.unlink(missing_ok=True)
            raise
        return ArtifactRef.model_validate(
            artifact.model_dump(
                include={
                    "artifact_id",
                    "kind",
                    "content_type",
                    "size",
                    "digest",
                    "metadata",
                }
            )
        )

    def get(self, artifact_id: str) -> tuple[WorkflowArtifact, bytes]:
        artifact = self._metadata_store.get(artifact_id)
        path = self._path_from_uri(artifact.storage_uri)
        content = path.read_bytes()
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if len(content) != artifact.size or digest != artifact.digest:
            raise ValueError("ARTIFACT_CONTENT_CORRUPTED")
        return artifact, content

    def _path_from_uri(self, storage_uri: str) -> Path:
        parsed = urlparse(storage_uri)
        if parsed.scheme != "file":
            raise ValueError("ARTIFACT_STORAGE_URI_UNSUPPORTED")
        path = Path(unquote(parsed.path)).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("ARTIFACT_PATH_OUTSIDE_ROOT")
        return path
