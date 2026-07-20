from dataclasses import dataclass, field
from typing import Literal

from apps.chatbi.models.dto.streaming import ModelMessage, ModelStreamChunk

# 旧名兼容（台账 E2）：Message/Chunk 已统一到 streaming 共享 DTO。
SQLGenerationMessage = ModelMessage
SQLGenerationModelChunk = ModelStreamChunk


@dataclass(frozen=True, slots=True)
class SQLGenerationData:
    """主 SQL 生成所需的稳定业务输入。"""

    record_id: int
    question: str
    database_type: str
    engine: str
    schema: str
    sample_data: str
    language: str
    assistant_name: str
    current_time: str
    rule: str = ""
    error_message: str = ""
    custom_prompt: str = ""
    terminologies: str = ""
    data_training: str = ""
    enable_query_limit: bool = True
    change_title: bool = False
    regenerate: bool = False
    history: list[SQLGenerationMessage] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SQLGenerationResult:
    sql: str
    tables: list[str] | None = None
    chart_type: str | None = None
    brief: str | None = None


@dataclass(frozen=True, slots=True)
class SQLGenerationEvent:
    kind: Literal["chunk", "completed"]
    content: str = ""
    reasoning_content: str = ""
    result: SQLGenerationResult | None = None
    error: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)


__all__ = [
    "SQLGenerationData",
    "SQLGenerationEvent",
    "SQLGenerationMessage",
    "SQLGenerationModelChunk",
    "SQLGenerationResult",
]
