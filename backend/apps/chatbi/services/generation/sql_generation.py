from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Protocol, cast

import orjson

from apps.chatbi.models import (
    ChatRecordResultProjection,
    SQLGenerationData,
    SQLGenerationEvent,
    SQLGenerationMessage,
    SQLGenerationModelChunk,
    SQLGenerationResult,
)
from apps.chatbi.services.conversation.chat_record_service import ChatRecordService


class SQLGenerationError(ValueError):
    """SQL 生成输入或模型结果不合法。"""


class SQLGenerationPromptBuilder(Protocol):
    def build(
        self,
        data: SQLGenerationData,
    ) -> list[SQLGenerationMessage]: ...


class SQLGenerationModelClient(Protocol):
    def stream(
        self,
        messages: list[SQLGenerationMessage],
    ) -> Iterator[SQLGenerationModelChunk]: ...


class SQLGenerationService:
    """统一主 SQL 提示词、模型流、响应解析和回答投影。"""

    def __init__(
        self,
        *,
        prompt_builder: SQLGenerationPromptBuilder,
        model_client: SQLGenerationModelClient,
        chat_record_service: ChatRecordService,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._model_client = model_client
        self._chat_record_service = chat_record_service

    def prepare(
        self,
        data: SQLGenerationData,
    ) -> list[SQLGenerationMessage]:
        self._validate_data(data)
        messages = self._prompt_builder.build(data)
        if not messages or any(not message.content.strip() for message in messages):
            raise SQLGenerationError("SQL_GENERATION_PROMPT_INVALID")
        return messages

    def generate(
        self,
        data: SQLGenerationData,
        messages: list[SQLGenerationMessage] | None = None,
    ) -> Iterator[SQLGenerationEvent]:
        self._validate_data(data)
        prepared_messages = self.prepare(data) if messages is None else messages
        if not prepared_messages or any(
            not message.content.strip() for message in prepared_messages
        ):
            raise SQLGenerationError("SQL_GENERATION_PROMPT_INVALID")

        full_content = ""
        full_reasoning = ""
        token_usage: dict[str, int] = {}
        for chunk in self._model_client.stream(prepared_messages):
            full_content += chunk.content
            full_reasoning += chunk.reasoning_content
            token_usage.update(chunk.token_usage)
            yield SQLGenerationEvent(
                kind="chunk",
                content=chunk.content,
                reasoning_content=chunk.reasoning_content,
                token_usage=dict(token_usage),
            )

        self._chat_record_service.project_result_by_id(
            data.record_id,
            ChatRecordResultProjection(
                answer=orjson.dumps({"content": full_content}).decode()
            ),
        )
        try:
            result = parse_sql_generation_result(full_content)
        except SQLGenerationError as exc:
            yield SQLGenerationEvent(
                kind="completed",
                content=full_content,
                reasoning_content=full_reasoning,
                error=str(exc),
                token_usage=token_usage,
            )
            return

        yield SQLGenerationEvent(
            kind="completed",
            content=full_content,
            reasoning_content=full_reasoning,
            result=result,
            token_usage=token_usage,
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
    "SQLGenerationModelClient",
    "SQLGenerationPromptBuilder",
    "SQLGenerationService",
    "parse_sql_generation_result",
]
