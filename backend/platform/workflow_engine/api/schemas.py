from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GraphQueryRequest(BaseModel):
    """独立 Graph Run 请求，不创建聊天历史。"""

    # Graph 请求必须显式遵守契约，禁止把会话归属等未知字段静默丢弃。
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    dataset_id: int = Field(gt=0)
    definition_version: Literal["minimal-v1", "v1"] = "minimal-v1"
    request_id: str | None = None
    run_id: str | None = None


class GraphChatQueryRequest(GraphQueryRequest):
    """交互式聊天 Graph 请求，chat_id 只由路径提供。"""

    definition_version: Literal["v1"] = "v1"


class GraphRunResponse(BaseModel):
    """Run 对外摘要，不暴露私有 Artifact 和内部控制字段。"""

    run_id: str
    record_id: int | None = None
    status: str
    current_node: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    context_summary: dict[str, Any] = Field(default_factory=dict)


class GraphPendingInteractionResponse(BaseModel):
    """等待用户处理的交互摘要，用于前端渲染澄清或选择控件。"""

    interaction_id: str
    run_id: str
    node_name: str
    label: str | None = None
    status: str
    prompt: str | None = None
    options: list[dict[str, Any]] = Field(default_factory=list)
    response_schema: dict[str, Any] = Field(default_factory=dict)
    allowed_update_paths: list[str] = Field(default_factory=list)


class GraphEventResponse(BaseModel):
    """单条可公开事件。"""

    event_id: str
    sequence: int
    event_type: str
    node_name: str | None = None
    public_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class GraphEventListResponse(BaseModel):
    """断点续传事件列表。"""

    events: list[GraphEventResponse]


class GraphTraceNodeResponse(BaseModel):
    """单个节点的前端 trace 摘要。"""

    name: str
    label: str
    status: str
    route_reason: str | None = None
    output: dict[str, Any] | bool | str | int | float | None = None


class GraphTraceResponse(BaseModel):
    """一次 Run 的节点级执行轨迹。"""

    run_id: str
    status: str
    current_node: str | None = None
    nodes: list[GraphTraceNodeResponse]


class InteractionResponseRequest(BaseModel):
    """用户对 pending interaction 的回答。"""

    response: dict[str, Any] = Field(default_factory=dict)


class ControlResponse(BaseModel):
    """控制接口的统一响应。"""

    run_id: str
    status: str
