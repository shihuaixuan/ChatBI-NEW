"""ChatBI 结果 Artifact 的公开存储与清理设施。

继承自已退役的 Graph Workflow Engine；表名、环境变量与磁盘目录保持不变。
"""

from apps.chatbi.adapters.artifact_store.cleanup import ArtifactCleanupService
from apps.chatbi.adapters.artifact_store.domain import ArtifactRef, WorkflowArtifact
from apps.chatbi.adapters.artifact_store.gateway import (
    WorkflowArtifactGateway,
    build_workflow_artifact_gateway,
)

__all__ = [
    "ArtifactCleanupService",
    "ArtifactRef",
    "WorkflowArtifact",
    "WorkflowArtifactGateway",
    "build_workflow_artifact_gateway",
]
