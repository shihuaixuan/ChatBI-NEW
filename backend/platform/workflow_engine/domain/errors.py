from typing import Any

from pydantic import BaseModel, Field


class NodeError(BaseModel):
    """跨 Runtime 边界传播的安全错误，不携带底层异常堆栈。"""

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1024)
    retryable: bool = False
    details_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
