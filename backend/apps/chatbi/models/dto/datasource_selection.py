from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class DatasourceSelectionCandidate:
    id: int
    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class DatasourceSelectionData:
    """数据源自动选择或模型选择所需的稳定业务输入。"""

    record_id: int
    question: str
    candidates: list[DatasourceSelectionCandidate]
    language: str
    assistant_name: str
    auto_select: bool


@dataclass(frozen=True, slots=True)
class DatasourceSelectionMessage:
    role: Literal["system", "human", "ai"]
    content: str


@dataclass(frozen=True, slots=True)
class DatasourceSelectionModelChunk:
    content: str = ""
    reasoning_content: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DatasourceSelectionEvent:
    kind: Literal["chunk", "completed"]
    content: str = ""
    reasoning_content: str = ""
    selected_datasource_id: int | None = None
    model_used: bool = False
    error: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)


__all__ = [
    "DatasourceSelectionCandidate",
    "DatasourceSelectionData",
    "DatasourceSelectionEvent",
    "DatasourceSelectionMessage",
    "DatasourceSelectionModelChunk",
]
