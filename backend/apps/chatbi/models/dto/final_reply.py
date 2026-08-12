from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class FinalReplyProjectionData:
    """最终回复组合所需的节点结果。"""

    answer: dict[str, Any] = field(default_factory=dict)
    recommendations: dict[str, Any] = field(default_factory=dict)
    chart: dict[str, Any] = field(default_factory=dict)


class FinalReplyProjectionResult(BaseModel):
    """前端消费的稳定最终回复契约。"""

    final_answer: str
    recommendations: list[str] = Field(default_factory=list)
    chart: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QueryFinalReplyProjectionData:
    """查询执行完成后的最终回答投影输入。"""

    answer_markdown: str
    execution: dict[str, Any] | None
    rows: list[dict[str, Any]] | None = None
    intent: dict[str, Any] = field(default_factory=dict)
    chart_type: Literal["table", "bar", "line", "pie"] | None = None
    x_field: str | None = None
    y_fields: list[str] = field(default_factory=list)


class QueryFinalReplyProjectionResult(BaseModel):
    """查询工具与执行器之间的稳定最终回答契约。"""

    answer: str
    chart: dict[str, Any] = Field(default_factory=dict)
    sql: str | None = None
    non_standard: bool = False


__all__ = [
    "FinalReplyProjectionData",
    "FinalReplyProjectionResult",
    "QueryFinalReplyProjectionData",
    "QueryFinalReplyProjectionResult",
]
