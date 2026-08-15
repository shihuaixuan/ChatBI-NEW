"""从用户明确表达中提取隐含的长期偏好候选。"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from apps.chatbi.errors import QuestionModelError
from apps.chatbi.models.dto.question_model import (
    QuestionModelInvocationData,
    QuestionModelJSONMode,
)
from apps.chatbi.services.understanding import StructuredModelService
from apps.memory.models.dto import (
    ClarificationMemoryEvent,
    MemoryCandidateInput,
    MemoryEvidenceType,
    MemoryLayer,
    MemoryType,
)
from apps.memory.services.memory_rules import validate_memory_payload

logger = logging.getLogger(__name__)


class MemoryExtractionItem(BaseModel):
    """模型输出的单条候选，不包含归属、来源和生命周期字段。"""

    model_config = ConfigDict(extra="forbid")

    memory_type: MemoryType
    memory_key: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    explicit: bool
    sensitive: bool = False
    confidence: float = Field(ge=0, le=1)


class MemoryExtractionOutput(BaseModel):
    """模型提取结果。"""

    model_config = ConfigDict(extra="forbid")

    candidates: list[MemoryExtractionItem] = Field(default_factory=list, max_length=5)


class MemoryCandidateExtractor:
    """把自然语言澄清回答转换为经过约束的用户记忆候选。"""

    def __init__(self, model_service: StructuredModelService) -> None:
        self._model_service = model_service

    def extract(self, event: ClarificationMemoryEvent) -> list[MemoryCandidateInput]:
        """提取隐含偏好；模型不可用或结果不合法时返回空结果。"""

        prompt = (
            '{"question": '
            + _json_string(event.question)
            + ', "answer": '
            + _json_string(event.answer_text)
            + "}"
        )
        try:
            result = self._model_service.invoke(
                QuestionModelInvocationData(
                    stage="MEMORY_EXTRACTION",
                    system_prompt=_MEMORY_EXTRACTION_SYSTEM_PROMPT,
                    user_prompt=prompt,
                    json_mode=QuestionModelJSONMode.STRICT,
                )
            )
            output = MemoryExtractionOutput.model_validate(result.payload)
        except (QuestionModelError, ValidationError, ValueError) as exc:
            logger.info("用户记忆模型输出不符合契约: %s", exc)
            return []

        evidence_type = _evidence_type(event.answer_text)
        candidates: list[MemoryCandidateInput] = []
        for item in output.candidates:
            if not item.explicit or item.sensitive:
                continue
            try:
                validate_memory_payload(item.payload)
                candidates.append(
                    MemoryCandidateInput(
                        layer=MemoryLayer.ATOM,
                        memory_type=item.memory_type,
                        memory_key=item.memory_key,
                        statement=item.statement,
                        payload=item.payload,
                        evidence_type=evidence_type,
                        evidence_text=event.answer_text,
                        source_ref=event.source_ref,
                        source_session_id=event.source_session_id,
                        confidence=item.confidence,
                        explicit=True,
                    )
                )
            except (ValueError, ValidationError) as exc:
                logger.info("忽略不安全的用户记忆候选: %s", exc)
        return candidates


def _json_string(value: str) -> str:
    """使用 JSON 字符串编码，避免用户文本破坏模型输入结构。"""

    return json.dumps(value, ensure_ascii=False)


def _evidence_type(answer_text: str) -> MemoryEvidenceType:
    if any(marker in answer_text for marker in ("不对", "不是", "错了", "纠正")):
        return MemoryEvidenceType.EXPLICIT_CORRECTION
    return MemoryEvidenceType.EXPLICIT_CONFIRMATION


_MEMORY_EXTRACTION_SYSTEM_PROMPT = """
你是用户长期记忆候选提取器。只从用户回答中提取用户明确表达、可能跨会话复用的个人偏好或纠正。

只输出 JSON 对象：
{
  "candidates": [
    {
      "memory_type": "semantic_preference | term_preference | query_shape_preference | presentation_preference | correction | negative_preference",
      "memory_key": "稳定的用户偏好键",
      "statement": "不包含数据资产绑定的中文描述",
      "payload": {"preference": "结构化偏好值"},
      "explicit": true,
      "sensitive": false,
      "confidence": 0.0
    }
  ]
}

要求：
- 只提取用户明确说出的“以后、默认、习惯、通常、每次”等长期意图；没有长期意图时返回空数组。
- 不要把一次临时查询、当前问题、模型推断或礼貌用语提取为记忆。
- 禁止输出 dataset_id、datasource_id、metric_id、dimension_id、表名、字段名、SQL、查询结果、权限信息和敏感个人信息。
- 不要输出团队或数据集范围；记忆始终属于当前用户。
- statement 和 payload 只描述用户偏好，不绑定任何当前数据资产。
""".strip()


__all__ = [
    "MemoryCandidateExtractor",
    "MemoryExtractionItem",
    "MemoryExtractionOutput",
]
