from __future__ import annotations

import json
from typing import Protocol

import orjson

from apps.chatbi.errors import (
    QuestionModelCallError,
    QuestionModelError,
    QuestionModelOutputError,
)
from apps.chatbi.models.dto.question_model import (
    QuestionModelInvocationData,
    QuestionModelJSONMode,
    QuestionModelResponse,
    QuestionModelResult,
)


class QuestionModelClient(Protocol):
    def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> QuestionModelResponse: ...


class StructuredModelService:
    """统一问题理解与结构化回答任务的模型调用边界。"""

    def __init__(self, model_client: QuestionModelClient) -> None:
        self._model_client = model_client

    @property
    def model_name(self) -> str:
        """返回可用于低基数 Trace 属性的客户端类型。"""

        return self._model_client.__class__.__name__

    def invoke(self, data: QuestionModelInvocationData) -> QuestionModelResult:
        self._validate(data)
        try:
            response = self._model_client.invoke(
                data.system_prompt,
                data.user_prompt,
            )
        except Exception as exc:
            raise QuestionModelCallError(
                "QUESTION_MODEL_CALL_FAILED",
                data.stage,
            ) from exc
        if not isinstance(response.content, str):
            raise QuestionModelOutputError(
                "QUESTION_MODEL_RESPONSE_CONTENT_INVALID",
                data.stage,
            )
        if not isinstance(response.usage_metadata, dict):
            raise QuestionModelOutputError(
                "QUESTION_MODEL_USAGE_METADATA_INVALID",
                data.stage,
            )
        content = response.content.strip()
        if not content:
            raise QuestionModelOutputError(
                "QUESTION_MODEL_EMPTY_RESPONSE",
                data.stage,
            )
        if data.json_mode is QuestionModelJSONMode.STRICT:
            payload = _parse_strict_json(content, data.stage)
        else:
            payload = _extract_json_object(content, data.stage)
        return QuestionModelResult(
            payload=payload,
            usage_metadata=dict(response.usage_metadata),
            raw_content=content,
        )

    @staticmethod
    def _validate(data: QuestionModelInvocationData) -> None:
        if not isinstance(data.stage, str) or not data.stage.strip():
            raise QuestionModelError("QUESTION_MODEL_STAGE_REQUIRED", "unknown")
        if not isinstance(data.system_prompt, str) or not data.system_prompt.strip():
            raise QuestionModelError(
                "QUESTION_MODEL_SYSTEM_PROMPT_REQUIRED",
                data.stage,
            )
        if not isinstance(data.user_prompt, str) or not data.user_prompt.strip():
            raise QuestionModelError(
                "QUESTION_MODEL_USER_PROMPT_REQUIRED",
                data.stage,
            )
        if not isinstance(data.json_mode, QuestionModelJSONMode):
            raise QuestionModelError(
                "QUESTION_MODEL_JSON_MODE_INVALID",
                data.stage,
            )


def _parse_strict_json(content: str, stage: str) -> dict[str, object]:
    try:
        payload = orjson.loads(content)
    except orjson.JSONDecodeError as exc:
        raise QuestionModelOutputError(
            "QUESTION_MODEL_OUTPUT_NOT_JSON",
            stage,
        ) from exc
    if not isinstance(payload, dict):
        raise QuestionModelOutputError(
            "QUESTION_MODEL_OUTPUT_NOT_OBJECT",
            stage,
        )
    return payload


def _extract_json_object(content: str, stage: str) -> dict[str, object]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise QuestionModelOutputError(
        "QUESTION_MODEL_OUTPUT_NOT_JSON_OBJECT",
        stage,
    )


__all__ = [
    "QuestionModelClient",
    "StructuredModelService",
]
