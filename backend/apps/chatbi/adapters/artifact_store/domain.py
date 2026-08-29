"""Artifact 引用与持久化元数据的领域模型。

继承自已退役的 Graph Workflow Engine，现由 ChatBI Agent 结果 Artifact 链路消费。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ArtifactRef(BaseModel):
    """Context 中保存的大对象轻量引用。"""

    artifact_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    size: int = Field(ge=0)
    digest: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowArtifact(ArtifactRef):
    """Artifact Store 中可持久化的完整元数据。

    正文由 storage_uri 指向的存储承载，避免查询结果或证据正文进入 Run 快照。
    """

    run_id: str = Field(min_length=1)
    storage_uri: str = Field(min_length=1)
    created_at: datetime
    temporary: bool = True


__all__ = [
    "ArtifactRef",
    "WorkflowArtifact",
]
