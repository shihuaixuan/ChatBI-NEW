from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, PositiveInt, model_validator

from apps.workflow_engine.domain.artifact import WorkflowArtifact
from apps.workflow_engine.domain.context import ContextPatch
from apps.workflow_engine.domain.errors import NodeError


class NodeResultStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    WAITING_INPUT = "waiting_input"


class NodeExecutionRequest(BaseModel):
    """Scheduler 传给 Handler 的稳定执行协议。"""

    run_id: str = Field(min_length=1)
    node_name: str = Field(min_length=1)
    attempt: PositiveInt
    idempotency_key: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    context_view: dict[str, Any] = Field(default_factory=dict)


class NodeExecutionResult(BaseModel):
    """Handler 的唯一返回类型。"""

    status: NodeResultStatus
    patch: ContextPatch = Field(default_factory=ContextPatch)
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    interaction: dict[str, Any] | None = None
    error: NodeError | None = None
    metrics: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "NodeExecutionResult":
        """让状态与载荷保持一致，避免失败结果丢失稳定错误码。"""

        if self.status is NodeResultStatus.FAILED and self.error is None:
            raise ValueError("失败结果必须包含错误")
        if self.status is not NodeResultStatus.FAILED and self.error is not None:
            raise ValueError("非失败结果不能包含错误")
        if self.status is NodeResultStatus.WAITING_INPUT and self.interaction is None:
            raise ValueError("等待输入结果必须包含交互定义")
        return self
