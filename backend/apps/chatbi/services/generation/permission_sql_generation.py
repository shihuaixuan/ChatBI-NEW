from __future__ import annotations

from collections.abc import Iterator

from apps.chatbi.errors import PermissionSQLGenerationError, SQLGenerationError
from apps.chatbi.models import (
    ChatRecordResultProjection,
    PermissionSQLGenerationData,
    SQLGenerationEvent,
    SQLGenerationMessage,
)
from apps.chatbi.services.conversation.chat_record_service import ChatRecordService
from apps.chatbi.services.generation.ports import (
    GenerationModelClient,
    PermissionSQLGenerationPromptBuilder,
)
from apps.chatbi.services.generation.sql_generation import parse_sql_generation_result
from apps.chatbi.services.generation.streaming import (
    StreamAccumulator,
    ensure_prompt_messages,
    stream_generation,
)


class PermissionSQLGenerationService:
    """统一权限 SQL 提示词、模型流、解析和结果投影。"""

    def __init__(
        self,
        *,
        prompt_builder: PermissionSQLGenerationPromptBuilder,
        model_client: GenerationModelClient,
        chat_record_service: ChatRecordService,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._model_client = model_client
        self._chat_record_service = chat_record_service

    def prepare(
        self,
        data: PermissionSQLGenerationData,
    ) -> list[SQLGenerationMessage]:
        self._validate_data(data)
        messages = self._prompt_builder.build(data)
        if not messages or any(not message.content.strip() for message in messages):
            raise PermissionSQLGenerationError(
                "PERMISSION_SQL_GENERATION_PROMPT_INVALID"
            )
        return messages

    def generate(
        self,
        data: PermissionSQLGenerationData,
        messages: list[SQLGenerationMessage] | None = None,
    ) -> Iterator[SQLGenerationEvent]:
        self._validate_data(data)
        prepared_messages = self.prepare(data) if messages is None else messages
        ensure_prompt_messages(prepared_messages, PermissionSQLGenerationError("PERMISSION_SQL_GENERATION_PROMPT_INVALID"))

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

        self._chat_record_service.project_result_by_id(
            data.record_id,
            ChatRecordResultProjection(sql=result.sql),
        )
        yield SQLGenerationEvent(
            kind="completed",
            content=stream.content,
            reasoning_content=stream.reasoning_content,
            result=result,
            token_usage=stream.token_usage,
        )

    @staticmethod
    def _validate_data(data: PermissionSQLGenerationData) -> None:
        if data.record_id <= 0:
            raise PermissionSQLGenerationError(
                "PERMISSION_SQL_GENERATION_RECORD_ID_INVALID"
            )
        if not data.sql.strip():
            raise PermissionSQLGenerationError(
                "PERMISSION_SQL_GENERATION_SQL_REQUIRED"
            )
        if not data.engine.strip():
            raise PermissionSQLGenerationError(
                "PERMISSION_SQL_GENERATION_ENGINE_REQUIRED"
            )
        if not data.filters:
            raise PermissionSQLGenerationError(
                "PERMISSION_SQL_GENERATION_FILTERS_REQUIRED"
            )
        if any(
            not item.table.strip() or not item.condition.strip()
            for item in data.filters
        ):
            raise PermissionSQLGenerationError(
                "PERMISSION_SQL_GENERATION_FILTER_INVALID"
            )


__all__ = [
    "PermissionSQLGenerationError",
    "PermissionSQLGenerationPromptBuilder",
    "PermissionSQLGenerationService",
]
