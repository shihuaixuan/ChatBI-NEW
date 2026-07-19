from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ChartGenerationMessage:
    role: Literal["system", "human", "ai"]
    content: str
    system_context: bool = False


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
    history: list[ChartGenerationMessage] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ChartGenerationModelChunk:
    content: str = ""
    reasoning_content: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)


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
    "ChartGenerationMessage",
    "ChartGenerationModelChunk",
]
