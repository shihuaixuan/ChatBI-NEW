from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class WorkflowDefinitionModel(SQLModel, table=True):
    """已发布流程定义。

    定义发布后不可覆盖，Run 启动时会记录 name/version/digest，后续即使激活版本变化，
    老 Run 仍按启动时的定义内容执行。
    """

    __tablename__ = "workflow_definition"
    __table_args__ = (
        UniqueConstraint("name", "version", name="ux_workflow_definition_version"),
        Index("idx_workflow_definition_active", "name", "active"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    name: str = Field(sa_column=Column(String(128), nullable=False))
    version: str = Field(sa_column=Column(String(64), nullable=False))
    digest: str = Field(sa_column=Column(String(128), nullable=False))
    definition: dict = Field(sa_column=Column(JSONB, nullable=False))
    active: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))
    metadata_json: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_by: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))


class WorkflowRunModel(SQLModel, table=True):
    """一次 Graph 执行实例。

    context 保存可恢复轻量状态；大对象只保存 artifact 引用。version 用于乐观锁，
    防止两个 Runtime 同时推进同一个 Run。
    """

    __tablename__ = "workflow_run"
    __table_args__ = (
        Index("idx_workflow_run_status", "oid", "status", text("updated_at DESC")),
        Index("idx_workflow_run_definition", "definition_name", "definition_version"),
        Index("idx_workflow_run_request", "request_id"),
        Index("idx_workflow_run_chat", "chat_id", text("created_at DESC")),
        Index("ux_workflow_run_record", "record_id", unique=True),
        # 交互式 Run 必须完整绑定会话和记录；独立 Run 则两者都不绑定。
        CheckConstraint(
            "(chat_id IS NULL AND record_id IS NULL) "
            "OR (chat_id IS NOT NULL AND record_id IS NOT NULL)",
            name="ck_workflow_run_chat_ownership",
        ),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    run_id: str = Field(sa_column=Column(String(64), nullable=False, unique=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    user_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    # 新增归属字段只记录后续显式绑定，不从既有 Run 的 JSON 上下文推断回填。
    chat_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    record_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    request_id: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    definition_name: str = Field(sa_column=Column(String(128), nullable=False))
    definition_version: str = Field(sa_column=Column(String(64), nullable=False))
    definition_digest: str = Field(sa_column=Column(String(128), nullable=False))
    status: str = Field(sa_column=Column(String(32), nullable=False))
    current_node: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    context: dict = Field(sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    request: dict = Field(sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    output: dict = Field(sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    error_code: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    version: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class NodeExecutionModel(SQLModel, table=True):
    """节点执行尝试记录。

    同一节点 attempt 只能写一次，用于支撑幂等重试和失败排障。
    """

    __tablename__ = "node_execution"
    __table_args__ = (
        UniqueConstraint("run_id", "node_name", "attempt", name="ux_node_execution_attempt"),
        Index("idx_node_execution_run", "run_id", "sequence"),
        Index("idx_node_execution_status", "status"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    run_id: str = Field(sa_column=Column(String(64), nullable=False))
    sequence: int = Field(sa_column=Column(Integer, nullable=False))
    node_name: str = Field(sa_column=Column(String(128), nullable=False))
    node_type: str = Field(sa_column=Column(String(32), nullable=False))
    handler: str = Field(sa_column=Column(String(256), nullable=False))
    attempt: int = Field(sa_column=Column(Integer, nullable=False))
    idempotency_key: str = Field(sa_column=Column(String(256), nullable=False))
    status: str = Field(sa_column=Column(String(32), nullable=False))
    input_summary: dict = Field(sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    output_summary: dict = Field(sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    route_summary: dict = Field(sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    error_code: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    duration_ms: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class WorkflowCheckpointModel(SQLModel, table=True):
    """节点边界处的可恢复快照。"""

    __tablename__ = "workflow_checkpoint"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="ux_workflow_checkpoint_sequence"),
        Index("idx_workflow_checkpoint_run", "run_id", text("sequence DESC")),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    checkpoint_id: str = Field(sa_column=Column(String(64), nullable=False, unique=True))
    run_id: str = Field(sa_column=Column(String(64), nullable=False))
    sequence: int = Field(sa_column=Column(Integer, nullable=False))
    node_name: str = Field(sa_column=Column(String(128), nullable=False))
    context: dict = Field(sa_column=Column(JSONB, nullable=False))
    definition_digest: str = Field(sa_column=Column(String(128), nullable=False))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class WorkflowEventModel(SQLModel, table=True):
    """面向审计、诊断和前端续传的追加事件。"""

    __tablename__ = "workflow_event"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="ux_workflow_event_sequence"),
        Index("idx_workflow_event_run", "run_id", "sequence"),
        Index("idx_workflow_event_publish", "publish_status", "sequence"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    event_id: str = Field(sa_column=Column(String(64), nullable=False, unique=True))
    run_id: str = Field(sa_column=Column(String(64), nullable=False))
    sequence: int = Field(sa_column=Column(Integer, nullable=False))
    event_type: str = Field(sa_column=Column(String(64), nullable=False))
    node_name: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    node_execution_id: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    public_payload: dict = Field(sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    internal_payload: dict = Field(sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    internal_payload_ref: str | None = Field(default=None, sa_column=Column(String(256), nullable=True))
    publish_status: str = Field(default="pending", sa_column=Column(String(32), nullable=False, server_default=text("'pending'")))
    publish_attempts: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class InteractionRequestModel(SQLModel, table=True):
    """等待用户输入的暂停请求。"""

    __tablename__ = "interaction_request"
    __table_args__ = (
        Index("idx_interaction_request_run", "run_id", text("created_at DESC")),
        Index("idx_interaction_request_pending", "run_id", "status"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    interaction_id: str = Field(sa_column=Column(String(64), nullable=False, unique=True))
    run_id: str = Field(sa_column=Column(String(64), nullable=False))
    node_name: str = Field(sa_column=Column(String(128), nullable=False))
    status: str = Field(sa_column=Column(String(32), nullable=False))
    prompt: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    response_schema: dict = Field(sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    allowed_update_paths: list[str] = Field(sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")))
    options: list[dict] = Field(sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")))
    response: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    answered_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class WorkflowArtifactModel(SQLModel, table=True):
    """大对象和敏感对象的元数据记录。"""

    __tablename__ = "workflow_artifact"
    __table_args__ = (
        Index("idx_workflow_artifact_run", "run_id", "kind"),
        Index("idx_workflow_artifact_temporary", "temporary", "created_at"),
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
