from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, PositiveInt, model_validator


class NodeType(str, Enum):
    """节点的运行语义。

    类型只描述 Runtime 应如何调度节点，不包含任何 ChatBI 业务含义。
    """

    CAPABILITY = "capability"
    DECISION = "decision"
    INTERACTION = "interaction"
    TRANSFORM = "transform"
    TERMINAL = "terminal"
    SUBGRAPH = "subgraph"


class RetryPolicy(BaseModel):
    """节点失败后的有限重试策略。"""

    max_attempts: PositiveInt = 1
    initial_delay_ms: int = Field(default=0, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0)
    max_delay_ms: int = Field(default=30_000, ge=0)


class WorkflowPolicies(BaseModel):
    """整张图共享的执行保护策略。

    所有限额都必须在流程发布前确定，避免运行时配置变化改变已启动 Run 的行为。
    """

    max_nodes_per_run: PositiveInt = 100
    max_loop_iterations: PositiveInt = 10
    run_timeout_ms: PositiveInt = 300_000
    default_retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)


class NodeDefinition(BaseModel):
    """一个可执行节点的静态定义。"""

    name: str = Field(min_length=1, max_length=128)
    type: NodeType
    handler: str = Field(min_length=1, max_length=256)
    input_mapping: dict[str, str] = Field(default_factory=dict)
    output_mapping: dict[str, str] = Field(default_factory=dict)
    retry_policy: RetryPolicy | None = None
    timeout_ms: PositiveInt | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EdgeDefinition(BaseModel):
    """节点之间的一条有向边。condition 为空时表示默认边。"""

    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    condition: str | None = Field(default=None, min_length=1, max_length=256)
    priority: int = Field(default=100, ge=0)
    label: str | None = Field(default=None, max_length=256)


class WorkflowDefinition(BaseModel):
    """一张不可变流程图的完整描述。

    本模型只做局部结构校验；可达性、注册引用和循环等全图规则由发布阶段的
    DefinitionValidator 负责，这样构造草稿和发布定义的职责不会混在一起。
    """

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    start_node: str = Field(min_length=1, max_length=128)
    nodes: dict[str, NodeDefinition] = Field(min_length=1)
    edges: list[EdgeDefinition] = Field(default_factory=list)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    policies: WorkflowPolicies = Field(default_factory=WorkflowPolicies)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_node_keys(self) -> "WorkflowDefinition":
        """确保字典键就是节点稳定标识，避免边和运行游标出现双重命名。"""

        for key, node in self.nodes.items():
            if key != node.name:
                raise ValueError("节点字典键必须与节点名称一致")
        return self
