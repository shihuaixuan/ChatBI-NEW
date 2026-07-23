from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import orjson

from apps.chatbi.errors import ChartGenerationError
from apps.chatbi.models import (
    ChartGenerationData,
    ChartGenerationEvent,
    ChatRecordResultProjection,
    ModelMessage,
)
from apps.chatbi.services.conversation.chat_record_service import ChatRecordService
from apps.chatbi.services.generation.ports import (
    ChartGenerationPromptBuilder,
    GenerationModelClient,
)
from apps.chatbi.services.generation.streaming import (
    StreamAccumulator,
    ensure_prompt_messages,
    stream_generation,
)


class ChartGenerationService:
    """统一图表提示词、模型流、配置标准化和记录投影。"""

    def __init__(
        self,
        *,
        prompt_builder: ChartGenerationPromptBuilder,
        model_client: GenerationModelClient,
        chat_record_service: ChatRecordService,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._model_client = model_client
        self._chat_record_service = chat_record_service

    def prepare(
        self,
        data: ChartGenerationData,
    ) -> list[ModelMessage]:
        self._validate_data(data)
        messages = self._prompt_builder.build(data)
        if not messages or any(not message.content.strip() for message in messages):
            raise ChartGenerationError("CHART_GENERATION_PROMPT_INVALID")
        return messages

    def generate(
        self,
        data: ChartGenerationData,
        messages: list[ModelMessage] | None = None,
    ) -> Iterator[ChartGenerationEvent]:
        self._validate_data(data)
        prepared_messages = self.prepare(data) if messages is None else messages
        ensure_prompt_messages(
            prepared_messages,
            ChartGenerationError("CHART_GENERATION_PROMPT_INVALID"),
        )

        stream = StreamAccumulator()
        for chunk in stream_generation(prepared_messages, self._model_client, stream):
            yield ChartGenerationEvent(
                kind="chunk",
                content=chunk.content,
                reasoning_content=chunk.reasoning_content,
                token_usage=dict(stream.token_usage),
            )

        answer = orjson.dumps({"content": stream.content}).decode()
        try:
            chart = _normalize_chart(stream.content)
        except ChartGenerationError as exc:
            self._chat_record_service.project_result_by_id(
                data.record_id,
                ChatRecordResultProjection(chart_answer=answer),
            )
            yield ChartGenerationEvent(
                kind="completed",
                content=stream.content,
                reasoning_content=stream.reasoning_content,
                error=str(exc),
                token_usage=stream.token_usage,
            )
            return

        self._chat_record_service.project_result_by_id(
            data.record_id,
            ChatRecordResultProjection(
                chart_answer=answer,
                chart=orjson.dumps(chart).decode(),
            ),
        )
        yield ChartGenerationEvent(
            kind="completed",
            content=stream.content,
            reasoning_content=stream.reasoning_content,
            chart=chart,
            token_usage=stream.token_usage,
        )

    @staticmethod
    def _validate_data(data: ChartGenerationData) -> None:
        if data.record_id <= 0:
            raise ChartGenerationError("CHART_GENERATION_RECORD_ID_INVALID")
        if not data.question.strip():
            raise ChartGenerationError("CHART_GENERATION_QUESTION_REQUIRED")
        if not data.sql.strip():
            raise ChartGenerationError("CHART_GENERATION_SQL_REQUIRED")


def _normalize_chart(content: str) -> dict[str, Any]:
    chart = _extract_chart_object(content)
    chart_type = chart.get("type")
    if chart_type == "error":
        reason = chart.get("reason")
        if isinstance(reason, str) and reason.strip():
            raise ChartGenerationError(reason.strip())
        raise ChartGenerationError(_invalid_chart_message(content))
    if not isinstance(chart_type, str) or not chart_type.strip():
        raise ChartGenerationError(_invalid_chart_message(content))

    columns = chart.get("columns")
    if columns:
        if not isinstance(columns, list):
            raise ChartGenerationError(_invalid_chart_message(content))
        for column in columns:
            _lower_value(column, content)

    axis = chart.get("axis")
    if axis:
        if not isinstance(axis, dict):
            raise ChartGenerationError(_invalid_chart_message(content))
        for key in ("x", "series"):
            item = axis.get(key)
            if item:
                _lower_value(item, content)

        y_axis = axis.get("y")
        if isinstance(y_axis, list):
            for item in y_axis:
                _lower_value(item, content, allow_missing=True)
        elif isinstance(y_axis, dict):
            _lower_value(y_axis, content, allow_missing=True)
        elif y_axis:
            raise ChartGenerationError(_invalid_chart_message(content))

        multi_quota = axis.get("multi-quota")
        if multi_quota:
            if not isinstance(multi_quota, dict):
                raise ChartGenerationError(_invalid_chart_message(content))
            values = multi_quota.get("value")
            if isinstance(values, list):
                normalized_values: list[Any] = []
                for value in values:
                    if isinstance(value, str):
                        normalized_values.append(value.lower())
                    elif value:
                        raise ChartGenerationError(_invalid_chart_message(content))
                    else:
                        normalized_values.append(value)
                multi_quota["value"] = normalized_values
            elif isinstance(values, str):
                multi_quota["value"] = values.lower()
            elif values:
                raise ChartGenerationError(_invalid_chart_message(content))
    return chart


def _extract_chart_object(content: str) -> dict[str, Any]:
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
    raise ChartGenerationError(_invalid_chart_message(content))


def _lower_value(
    item: object,
    content: str,
    *,
    allow_missing: bool = False,
) -> None:
    if not isinstance(item, dict):
        raise ChartGenerationError(_invalid_chart_message(content))
    value = item.get("value")
    if value is None and allow_missing:
        return
    if not isinstance(value, str):
        raise ChartGenerationError(_invalid_chart_message(content))
    item["value"] = value.lower()


def _invalid_chart_message(content: str) -> str:
    return orjson.dumps(
        {
            "message": "Cannot parse chart config from answer",
            "traceback": "Cannot parse chart config from answer:\n" + content,
        }
    ).decode()


__all__ = [
    "ChartGenerationError",
    "ChartGenerationPromptBuilder",
    "ChartGenerationService",
]
