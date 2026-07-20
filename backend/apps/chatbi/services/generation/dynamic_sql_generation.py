from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from apps.chatbi.models import (
    DynamicSQLGenerationData,
    SQLGenerationEvent,
    SQLGenerationMessage,
    SQLGenerationModelChunk,
)
from apps.chatbi.services.sql_generation_service import (
    SQLGenerationError,
    parse_sql_generation_result,
)


class DynamicSQLGenerationError(ValueError):
    """动态 SQL 生成输入不合法。"""


class DynamicSQLGenerationPromptBuilder(Protocol):
    def build(
        self,
        data: DynamicSQLGenerationData,
    ) -> list[SQLGenerationMessage]: ...


class DynamicSQLGenerationModelClient(Protocol):
    def stream(
        self,
        messages: list[SQLGenerationMessage],
    ) -> Iterator[SQLGenerationModelChunk]: ...


class DynamicSQLGenerationService:
    """统一动态 SQL 提示词、模型流和结构化响应解析。"""

    def __init__(
        self,
        *,
        prompt_builder: DynamicSQLGenerationPromptBuilder,
        model_client: DynamicSQLGenerationModelClient,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._model_client = model_client

    def prepare(
        self,
        data: DynamicSQLGenerationData,
    ) -> list[SQLGenerationMessage]:
        self._validate_data(data)
        messages = self._prompt_builder.build(data)
        if not messages or any(not message.content.strip() for message in messages):
            raise DynamicSQLGenerationError("DYNAMIC_SQL_GENERATION_PROMPT_INVALID")
        return messages

    def generate(
        self,
        data: DynamicSQLGenerationData,
        messages: list[SQLGenerationMessage] | None = None,
    ) -> Iterator[SQLGenerationEvent]:
        self._validate_data(data)
        prepared_messages = self.prepare(data) if messages is None else messages
        if not prepared_messages or any(
            not message.content.strip() for message in prepared_messages
        ):
            raise DynamicSQLGenerationError("DYNAMIC_SQL_GENERATION_PROMPT_INVALID")

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
    def _validate_data(data: DynamicSQLGenerationData) -> None:
        if not data.sql.strip():
            raise DynamicSQLGenerationError("DYNAMIC_SQL_GENERATION_SQL_REQUIRED")
        if not data.engine.strip():
            raise DynamicSQLGenerationError("DYNAMIC_SQL_GENERATION_ENGINE_REQUIRED")
        if not data.subqueries:
            raise DynamicSQLGenerationError(
                "DYNAMIC_SQL_GENERATION_SUBQUERIES_REQUIRED"
            )
        if any(
            not mapping.table.strip() or not mapping.query.strip()
            for mapping in data.subqueries
        ):
            raise DynamicSQLGenerationError("DYNAMIC_SQL_GENERATION_MAPPING_INVALID")


__all__ = [
    "DynamicSQLGenerationError",
    "DynamicSQLGenerationModelClient",
    "DynamicSQLGenerationPromptBuilder",
    "DynamicSQLGenerationService",
]
