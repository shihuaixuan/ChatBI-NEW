from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

AnswerGenerationMode = Literal["reject", "chitchat", "generate"]


@dataclass(frozen=True, slots=True)
class AnswerGenerationData:
    """结构化回答生成输入。"""

    mode: AnswerGenerationMode
    question: str
    projection: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AnswerGenerationPrompt:
    """回答模型使用的系统提示词和用户提示词。"""

    system_prompt: str
    user_prompt: str


class AnswerGenerationResult(BaseModel):
    """ChatBI 结构化回答输出契约。"""

    answer: str
    warnings: list[str] = Field(default_factory=list)
    render_type: str = "text"
    citations: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "AnswerGenerationData",
    "AnswerGenerationMode",
    "AnswerGenerationPrompt",
    "AnswerGenerationResult",
]
