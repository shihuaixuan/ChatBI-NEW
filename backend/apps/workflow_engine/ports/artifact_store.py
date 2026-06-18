from typing import Protocol

from apps.workflow_engine.domain.artifact import WorkflowArtifact


class ArtifactStore(Protocol):
    """大型中间产物的存储端口。"""

    def put(self, artifact: WorkflowArtifact, content: bytes) -> WorkflowArtifact: ...

    def get(self, artifact_id: str) -> tuple[WorkflowArtifact, bytes]: ...
