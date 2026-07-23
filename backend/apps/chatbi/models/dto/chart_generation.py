from dataclasses import dataclass, field
from typing import Any, Literal

from apps.chatbi.models.dto.streaming import ModelMessage


@dataclass(frozen=True, slots=True)
class ChartGenerationData:
    """图表生成所需的稳定业务输入。"""

    record_id: int
    question: str
    sql: str
    schema: str
    chart_type: str
    language: str
    assistant_name: str
    rule: str = ""
    history: list[ModelMessage] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ChartGenerationEvent:
    kind: Literal["chunk", "completed"]
    content: str = ""
    reasoning_content: str = ""
    chart: dict[str, Any] | None = None
    error: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)


__all__ = [
    "ChartGenerationData",
    "ChartGenerationEvent",
]
