"""与模型厂商无关的 Tool 定义。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolAnnotations(BaseModel):
    """供模型适配器使用的行为提示，不参与业务授权。"""

    model_config = ConfigDict(frozen=True)

    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True


class ToolDefinition(BaseModel):
    """Tool 对模型公开的稳定定义。"""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    title: str | None = None
    description: str = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    annotations: ToolAnnotations = Field(default_factory=ToolAnnotations)


__all__ = ["ToolAnnotations", "ToolDefinition"]
