from dataclasses import dataclass, field
from typing import Literal

from apps.chatbi.models.dto.chat_record import ChatRecordAuxiliaryType
from apps.chatbi.models.dto.streaming import ModelMessage, ModelStreamChunk


@dataclass(frozen=True, slots=True)
class AnalysisPredictionGenerationData:
    """分析或预测模型调用所需的稳定业务输入。"""

    record_id: int
    generation_type: ChatRecordAuxiliaryType
    fields: str
    data: str
    language: str
    assistant_name: str
    custom_prompt: str = ""
    terminologies: str = ""


# 旧名兼容（台账 E2）。
AnalysisPredictionMessage = ModelMessage
AnalysisPredictionModelChunk = ModelStreamChunk


@dataclass(frozen=True, slots=True)
class AnalysisPredictionGenerationEvent:
    kind: Literal["chunk", "completed"]
    content: str = ""
    reasoning_content: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)


__all__ = [
    "AnalysisPredictionGenerationData",
    "AnalysisPredictionGenerationEvent",
    "AnalysisPredictionMessage",
    "AnalysisPredictionModelChunk",
]
