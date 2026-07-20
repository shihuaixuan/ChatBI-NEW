from dataclasses import dataclass, field
from typing import Literal

from apps.chatbi.models.dto.streaming import ModelMessage, ModelStreamChunk


@dataclass(frozen=True, slots=True)
class RecommendedQuestionGenerationData:
    """生成推荐问题所需的稳定业务输入。"""

    record_id: int
    question: str
    schema: str
    datasource_id: int | None
    language: str
    assistant_name: str
    articles_number: int = 4


# 旧名兼容（台账 E2）。
RecommendedQuestionMessage = ModelMessage
RecommendedQuestionModelChunk = ModelStreamChunk


@dataclass(frozen=True, slots=True)
class RecommendedQuestionGenerationEvent:
    kind: Literal["chunk", "completed"]
    content: str = ""
    reasoning_content: str = ""
    recommended_question: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)


__all__ = [
    "RecommendedQuestionGenerationData",
    "RecommendedQuestionGenerationEvent",
    "RecommendedQuestionMessage",
    "RecommendedQuestionModelChunk",
]
