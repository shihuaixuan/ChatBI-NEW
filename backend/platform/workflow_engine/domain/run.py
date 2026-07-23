from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from sqlbot_platform.workflow_engine.domain.context import WorkflowContext


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowRun(BaseModel):
    """一次图执行的领域快照，与数据库 ORM 模型保持隔离。"""

    run_id: str = Field(min_length=1)
    definition_name: str = Field(min_length=1)
    definition_version: str = Field(min_length=1)
    definition_digest: str = Field(min_length=1)
    status: RunStatus = RunStatus.CREATED
    current_node: str | None = None
    context: WorkflowContext = Field(default_factory=WorkflowContext)
    version: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
