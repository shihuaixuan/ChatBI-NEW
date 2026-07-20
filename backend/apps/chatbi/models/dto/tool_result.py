"""能力调用统一结果信封（历史形状，R2 流式骨架统一时评估收敛）。"""

from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """一次领域能力调用的成功与否、结构化 payload 与错误分类。

    Graph 与 Agent 两条问数链路共用；不承载运行时概念。
    """

    success: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    error_code: str | None = None


__all__ = ["ToolResult"]
