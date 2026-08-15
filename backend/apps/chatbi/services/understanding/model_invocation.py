from __future__ import annotations

import json
import time
from collections.abc import Mapping
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
        """返回模型名称；客户端未提供名称时回退到客户端类型。"""

        configured_name = getattr(self._model_client, "model_name", None)
        if isinstance(configured_name, str) and configured_name.strip():
            return configured_name.strip()
        return self._model_client.__class__.__name__

    def invoke(self, data: QuestionModelInvocationData) -> QuestionModelResult:
        self._validate(data)
        started_at = time.perf_counter()
        try:
            response = self._model_client.invoke(
                data.system_prompt,
                data.user_prompt,
            )
        except Exception as exc:
            details = _build_model_call_error_details(
                stage=data.stage,
                client=self._model_client,
                error=exc,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            )
            raise QuestionModelCallError(
                "QUESTION_MODEL_CALL_FAILED",
                data.stage,
                details=details,
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


def _build_model_call_error_details(
    *,
    stage: str,
    client: QuestionModelClient,
    error: Exception,
    elapsed_ms: int,
) -> dict[str, object]:
    """提取不含提示词、响应正文和密钥的模型调用诊断信息。"""

    response = getattr(error, "response", None)
    status_code = _first_int(
        getattr(error, "status_code", None),
        getattr(response, "status_code", None),
    )
    request_id = _request_id(error, response)
    timeout_type = None
    error_name = type(error).__name__
    if isinstance(error, TimeoutError) or "timeout" in error_name.lower():
        timeout_type = error_name

    details: dict[str, object] = {
        "stage": stage,
        "client_type": type(client).__name__,
        "model": _safe_client_value(client, "model_name"),
        "provider": _safe_client_value(client, "provider_name"),
        "exception_type": error_name,
        "exception_module": type(error).__module__,
        "elapsed_ms": elapsed_ms,
    }
    if status_code is not None:
        details["http_status_code"] = status_code
    if request_id:
        details["provider_request_id"] = request_id
    if timeout_type:
        details["timeout_type"] = timeout_type
    return details


def _safe_client_value(client: QuestionModelClient, name: str) -> str | None:
    """只读取客户端显式提供的低敏标识，不读取模型配置或密钥。"""

    value = getattr(client, name, None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _first_int(*values: object) -> int | None:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _request_id(error: Exception, response: object) -> str | None:
    for value in (getattr(error, "request_id", None), getattr(error, "requestId", None)):
        if isinstance(value, str) and value.strip():
            return value.strip()
    headers = getattr(response, "headers", None)
    if isinstance(headers, Mapping):
        for key in ("x-request-id", "request-id", "x-amzn-requestid"):
            value = headers.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None

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
