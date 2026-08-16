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
    claims: list[dict[str, Any]] = Field(default_factory=list)
    caliber_card: dict[str, Any] = Field(default_factory=dict)
    chart: dict[str, Any] = Field(default_factory=dict)
    chart_spec: dict[str, Any] = Field(default_factory=dict)
    degraded: bool = False

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """空的新字段不改变旧回答 JSON，Composer 产出时保留扩展字段。"""

        payload = super().model_dump(*args, **kwargs)
        if not self.claims:
            payload.pop("claims", None)
        if not self.caliber_card:
            payload.pop("caliber_card", None)
        if not self.chart:
            payload.pop("chart", None)
        if not self.chart_spec:
            payload.pop("chart_spec", None)
        if not self.degraded:
            payload.pop("degraded", None)
        return payload


__all__ = [
    "AnswerGenerationData",
    "AnswerGenerationMode",
    "AnswerGenerationPrompt",
    "AnswerGenerationResult",
]
