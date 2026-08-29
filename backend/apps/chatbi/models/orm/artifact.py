"""ChatBI 结果 Artifact 元数据与正文清理任务的 SQLModel 表定义。

表继承自已退役的 Graph Workflow Engine，表名与存量数据保持不变。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class WorkflowArtifactModel(SQLModel, table=True):
    """大对象和敏感对象的元数据记录。"""

    __tablename__ = "workflow_artifact"
    __table_args__ = (
        Index("idx_workflow_artifact_run", "run_id", "kind"),
        Index("idx_workflow_artifact_temporary", "temporary", "created_at"),
        Index(
            "ux_workflow_artifact_idempotency",
            "run_id",
            "kind",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    artifact_id: str = Field(sa_column=Column(String(64), nullable=False, unique=True))
    run_id: str = Field(sa_column=Column(String(64), nullable=False))
    kind: str = Field(sa_column=Column(String(64), nullable=False))
    content_type: str = Field(sa_column=Column(String(128), nullable=False))
    size: int = Field(sa_column=Column(BigInteger, nullable=False))
    digest: str = Field(sa_column=Column(String(128), nullable=False))
    storage_uri: str = Field(sa_column=Column(String(512), nullable=False))
    metadata_json: dict = Field(sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    idempotency_key: str | None = Field(
        default=None,
        sa_column=Column(String(256), nullable=True),
    )
    temporary: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class WorkflowArtifactCleanupModel(SQLModel, table=True):
    """记录会话删除后需要清理的 Artifact 正文。"""

    __tablename__ = "workflow_artifact_cleanup"
    __table_args__ = (
        Index("idx_workflow_artifact_cleanup_status", "status", "updated_at"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    artifact_id: str = Field(sa_column=Column(String(64), nullable=False, unique=True))
    storage_uri: str = Field(sa_column=Column(String(512), nullable=False))
    # 状态与尝试次数持久化，保证正文清理失败后可以显式重试。
    status: str = Field(
        default="pending",
        sa_column=Column(String(32), nullable=False, server_default=text("'pending'")),
    )
    attempts: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    last_error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


__all__ = [
    "WorkflowArtifactCleanupModel",
    "WorkflowArtifactModel",
]
