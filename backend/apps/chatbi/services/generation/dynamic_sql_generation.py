from __future__ import annotations

from collections.abc import Iterator

from apps.chatbi.errors import DynamicSQLGenerationError, SQLGenerationError
from apps.chatbi.models import (
    DynamicSQLGenerationData,
    ModelMessage,
    SQLGenerationEvent,
)
from apps.chatbi.services.generation.ports import (
    DynamicSQLGenerationPromptBuilder,
    GenerationModelClient,
)
from apps.chatbi.services.generation.sql_generation import parse_sql_generation_result
from apps.chatbi.services.generation.streaming import (
    StreamAccumulator,
    ensure_prompt_messages,
    stream_generation,
)


class DynamicSQLGenerationService:
    """统一动态 SQL 提示词、模型流和结构化响应解析。"""

    def __init__(
        self,
        *,
        prompt_builder: DynamicSQLGenerationPromptBuilder,
        model_client: GenerationModelClient,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._model_client = model_client

    def prepare(
        self,
        data: DynamicSQLGenerationData,
    ) -> list[ModelMessage]:
        self._validate_data(data)
        messages = self._prompt_builder.build(data)
        if not messages or any(not message.content.strip() for message in messages):
            raise DynamicSQLGenerationError("DYNAMIC_SQL_GENERATION_PROMPT_INVALID")
        return messages

    def generate(
        self,
        data: DynamicSQLGenerationData,
        messages: list[ModelMessage] | None = None,
    ) -> Iterator[SQLGenerationEvent]:
        self._validate_data(data)
        prepared_messages = self.prepare(data) if messages is None else messages
        ensure_prompt_messages(prepared_messages, DynamicSQLGenerationError("DYNAMIC_SQL_GENERATION_PROMPT_INVALID"))

        stream = StreamAccumulator()
        for chunk in stream_generation(prepared_messages, self._model_client, stream):
            yield SQLGenerationEvent(
                kind="chunk",
                content=chunk.content,
                reasoning_content=chunk.reasoning_content,
                token_usage=dict(stream.token_usage),
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
    "DynamicSQLGenerationPromptBuilder",
    "DynamicSQLGenerationService",
]
