from typing import Any

from pydantic import BaseModel, Field

from sqlbot_platform.workflow_engine.domain.artifact import ArtifactRef


class ControlContext(BaseModel):
    """只允许 Runtime 更新的控制流字段。"""

    current_node: str | None = None
    previous_node: str | None = None
    executed_nodes: int = Field(default=0, ge=0)
    loop_iterations: dict[str, int] = Field(default_factory=dict)
    pending_interaction_id: str | None = None
    active_ms: int = Field(default=0, ge=0)


class WorkflowContext(BaseModel):
    """一次 Run 的可恢复轻量上下文。

    request 保存服务端注入的不可变身份和请求信息；conversation 与 variables
    保存可演进业务状态；artifacts 只保存引用；control 由 Runtime 独占管理。
    """

    request: dict[str, Any] = Field(default_factory=dict)
    conversation: dict[str, Any] = Field(default_factory=dict)
    variables: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    control: ControlContext = Field(default_factory=ControlContext)


class ContextPatch(BaseModel):
    """节点声明的局部状态变更，不允许节点原地修改共享 Context。"""

    set_values: dict[str, Any] = Field(default_factory=dict)
    append_values: dict[str, list[Any]] = Field(default_factory=dict)
    remove_paths: list[str] = Field(default_factory=list)
    artifact_updates: dict[str, ArtifactRef] = Field(default_factory=dict)
