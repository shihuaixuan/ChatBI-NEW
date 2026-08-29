from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse
from uuid import uuid4

from sqlmodel import Session

from apps.chatbi.adapters.artifact_store.domain import (
    ArtifactRef,
    WorkflowArtifact,
)
from apps.chatbi.adapters.artifact_store.repository import ArtifactRepository


def workflow_artifact_root() -> Path:
    """返回 Artifact 正文的唯一根目录。"""

    return (
        Path(
            os.getenv(
                "SQLBOT_WORKFLOW_ARTIFACT_DIR",
                str(
                    Path(__file__).resolve().parents[4] / "data" / "workflow_artifacts"
                ),
            )
        )
        .expanduser()
        .resolve()
    )


def _artifact_path_from_uri(storage_uri: str, root: Path) -> Path:
    """解析并校验 Artifact 文件必须位于指定根目录内。"""

    parsed = urlparse(storage_uri)
    if parsed.scheme != "file":
        raise ValueError("ARTIFACT_STORAGE_URI_UNSUPPORTED")
    path = Path(unquote(parsed.path)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("ARTIFACT_PATH_OUTSIDE_ROOT")
    return path


def delete_artifact_body(storage_uri: str, root: Path | None = None) -> None:
    """只允许删除 Artifact 根目录内的 file URI。"""

    path = _artifact_path_from_uri(storage_uri, root or workflow_artifact_root())
    path.unlink(missing_ok=True)


class ArtifactMetadataStore(Protocol):
    """Artifact 元数据存储协议。"""

    def put(self, artifact: WorkflowArtifact) -> WorkflowArtifact: ...

    def get(self, artifact_id: str) -> WorkflowArtifact: ...

    def find_by_idempotency_key(
        self,
        *,
        run_id: str,
        kind: str,
        idempotency_key: str,
    ) -> WorkflowArtifact | None: ...


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

    def find_by_idempotency_key(
        self,
        *,
        run_id: str,
        kind: str,
        idempotency_key: str,
    ) -> WorkflowArtifact | None:
        with self._session_factory() as session:
            return ArtifactRepository(session).find_by_idempotency_key(
                run_id=run_id,
                kind=kind,
                idempotency_key=idempotency_key,
            )


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

    def find_by_idempotency_key(
        self,
        *,
        run_id: str,
        kind: str,
        idempotency_key: str,
    ) -> tuple[WorkflowArtifact, bytes] | None:
        artifact = self._metadata_store.find_by_idempotency_key(
            run_id=run_id,
            kind=kind,
            idempotency_key=idempotency_key,
        )
        if artifact is None:
            return None
        return self.get(artifact.artifact_id)

    def _path_from_uri(self, storage_uri: str) -> Path:
        return _artifact_path_from_uri(storage_uri, self._root)
