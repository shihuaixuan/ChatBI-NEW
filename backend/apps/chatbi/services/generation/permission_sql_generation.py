from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from apps.chatbi.models import (
    ChatRecordResultProjection,
    PermissionSQLGenerationData,
    SQLGenerationEvent,
    SQLGenerationMessage,
    SQLGenerationModelChunk,
)
from apps.chatbi.services.conversation.chat_record_service import ChatRecordService
from apps.chatbi.services.generation.sql_generation import (
    SQLGenerationError,
    parse_sql_generation_result,
)


class PermissionSQLGenerationError(ValueError):
    """权限 SQL 生成输入不合法。"""


class PermissionSQLGenerationPromptBuilder(Protocol):
    def build(
        self,
        data: PermissionSQLGenerationData,
    ) -> list[SQLGenerationMessage]: ...


class PermissionSQLGenerationModelClient(Protocol):
    def stream(
        self,
        messages: list[SQLGenerationMessage],
    ) -> Iterator[SQLGenerationModelChunk]: ...


class PermissionSQLGenerationService:
    """统一权限 SQL 提示词、模型流、解析和结果投影。"""

    def __init__(
        self,
        *,
        prompt_builder: PermissionSQLGenerationPromptBuilder,
        model_client: PermissionSQLGenerationModelClient,
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
        if not prepared_messages or any(
            not message.content.strip() for message in prepared_messages
        ):
            raise PermissionSQLGenerationError(
                "PERMISSION_SQL_GENERATION_PROMPT_INVALID"
            )

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

        self._chat_record_service.project_result_by_id(
            data.record_id,
            ChatRecordResultProjection(sql=result.sql),
        )
        yield SQLGenerationEvent(
            kind="completed",
            content=full_content,
            reasoning_content=full_reasoning,
            result=result,
            token_usage=token_usage,
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
    "PermissionSQLGenerationModelClient",
    "PermissionSQLGenerationPromptBuilder",
    "PermissionSQLGenerationService",
]
