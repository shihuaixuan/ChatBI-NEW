from __future__ import annotations

import json
from collections.abc import Iterator
from typing import cast

import orjson

from apps.chatbi.errors import SQLGenerationError
from apps.chatbi.models import (
    ModelMessage,
    SQLGenerationData,
    SQLGenerationEvent,
    SQLGenerationResult,
)
from apps.chatbi.services.generation.ports import (
    GenerationModelClient,
    SQLGenerationPromptBuilder,
)
from apps.chatbi.services.generation.streaming import (
    StreamAccumulator,
    ensure_prompt_messages,
    stream_generation,
)
from apps.conversation import ChatRecordResultProjection, ChatRecordService


class SQLGenerationService:
    """统一主 SQL 提示词、模型流、响应解析和回答投影。"""

    def __init__(
        self,
        *,
        prompt_builder: SQLGenerationPromptBuilder,
        model_client: GenerationModelClient,
        chat_record_service: ChatRecordService,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._model_client = model_client
        self._chat_record_service = chat_record_service

    def prepare(
        self,
        data: SQLGenerationData,
    ) -> list[ModelMessage]:
        self._validate_data(data)
        messages = self._prompt_builder.build(data)
        if not messages or any(not message.content.strip() for message in messages):
            raise SQLGenerationError("SQL_GENERATION_PROMPT_INVALID")
        return messages

    def generate(
        self,
        data: SQLGenerationData,
        messages: list[ModelMessage] | None = None,
    ) -> Iterator[SQLGenerationEvent]:
        self._validate_data(data)
        prepared_messages = self.prepare(data) if messages is None else messages
        ensure_prompt_messages(prepared_messages, SQLGenerationError("SQL_GENERATION_PROMPT_INVALID"))

        stream = StreamAccumulator()
        for chunk in stream_generation(prepared_messages, self._model_client, stream):
            yield SQLGenerationEvent(
                kind="chunk",
                content=chunk.content,
                reasoning_content=chunk.reasoning_content,
                token_usage=dict(stream.token_usage),
            )

        self._chat_record_service.project_result_by_id(
            data.record_id,
            ChatRecordResultProjection(
                answer=orjson.dumps({"content": stream.content}).decode()
            ),
        )
        try:
            result = parse_sql_generation_result(stream.content)
        except SQLGenerationError as exc:
            yield SQLGenerationEvent(
                kind="completed",
                content=stream.content,
                reasoning_content=stream.reasoning_content,
                error=str(exc),
                token_usage=stream.token_usage,
            )
            return

        yield SQLGenerationEvent(
            kind="completed",
            content=stream.content,
            reasoning_content=stream.reasoning_content,
            result=result,
            token_usage=stream.token_usage,
        )

    @staticmethod
    def _validate_data(data: SQLGenerationData) -> None:
        if data.record_id <= 0:
            raise SQLGenerationError("SQL_GENERATION_RECORD_ID_INVALID")
        if not data.question.strip():
            raise SQLGenerationError("SQL_GENERATION_QUESTION_REQUIRED")
        if not data.database_type.strip() or not data.engine.strip():
            raise SQLGenerationError("SQL_GENERATION_ENGINE_REQUIRED")
        if not data.schema.strip():
            raise SQLGenerationError("SQL_GENERATION_SCHEMA_REQUIRED")
        if not data.current_time.strip():
            raise SQLGenerationError("SQL_GENERATION_CURRENT_TIME_REQUIRED")


def parse_sql_generation_result(content: str) -> SQLGenerationResult:
    payload = _extract_first_json_value(content)
    if payload is None:
        raise SQLGenerationError(
            orjson.dumps(
                {
                    "message": "SQL answer is not a valid json object",
                    "traceback": "SQL answer is not a valid json object:\n" + content,
                }
            ).decode()
        )
    try:
        if not isinstance(payload, dict):
            raise TypeError
        if not payload["success"]:
            message = payload["message"]
            if not isinstance(message, str):
                raise TypeError
            raise SQLGenerationError(message)
        sql = payload["sql"]
        if not isinstance(sql, str):
            raise TypeError
        tables_value = payload.get("tables")
        if tables_value is None:
            tables = None
        elif isinstance(tables_value, list) and all(
            isinstance(table, str) for table in tables_value
        ):
            tables = tables_value
        else:
            raise TypeError
    except SQLGenerationError:
        raise
    except (KeyError, TypeError):
        raise SQLGenerationError(
            orjson.dumps(
                {
                    "message": "Cannot parse sql from answer",
                    "traceback": "Cannot parse sql from answer:\n" + content,
                }
            ).decode()
        ) from None

    if not sql.strip():
        raise SQLGenerationError("SQL query is empty")
    chart_type = payload.get("chart-type")
    brief = payload.get("brief")
    return SQLGenerationResult(
        sql=sql,
        tables=tables,
        chart_type=chart_type if isinstance(chart_type, str) else None,
        brief=brief if isinstance(brief, str) else None,
    )


def _extract_first_json_value(content: str) -> object | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        return cast(object, payload)
    return None


__all__ = [
    "SQLGenerationError",
    "SQLGenerationPromptBuilder",
    "SQLGenerationService",
    "parse_sql_generation_result",
]
