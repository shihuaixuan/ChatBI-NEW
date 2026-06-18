from typing import Any, Literal

from pydantic import BaseModel, Field


class GraphQueryRequest(BaseModel):
    """创建 Graph Run 的请求体。"""

    question: str = Field(min_length=1)
    datasource_id: int = Field(gt=0)
    definition_version: Literal["minimal-v1", "v1"] = "minimal-v1"
    request_id: str | None = None
    run_id: str | None = None


class GraphRunResponse(BaseModel):
    """Run 对外摘要，不暴露私有 Artifact 和内部控制字段。"""

    run_id: str
    status: str
    current_node: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    context_summary: dict[str, Any] = Field(default_factory=dict)


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
