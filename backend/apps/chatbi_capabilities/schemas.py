from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """能力层统一结果信封。

    图主线（chatbi_workflow）与 Agentic 两条链路共用；不承载运行时概念，
    仅表达一次领域能力调用的成功与否、结构化 payload 与错误分类。
    """

    success: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    error_code: str | None = None
