from dataclasses import dataclass, field
from typing import Literal

from apps.conversation import ChatRecordAuxiliaryType


@dataclass(frozen=True, slots=True)
class AnalysisPredictionGenerationData:
    """分析或预测模型调用所需的稳定业务输入。"""

    record_id: int
    generation_type: ChatRecordAuxiliaryType
    fields: str
    data: str
    language: str
    assistant_name: str
    terminologies: str = ""


@dataclass(frozen=True, slots=True)
class AnalysisPredictionGenerationEvent:
    kind: Literal["chunk", "completed"]
    content: str = ""
    reasoning_content: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)


__all__ = [
    "AnalysisPredictionGenerationData",
    "AnalysisPredictionGenerationEvent",
]
