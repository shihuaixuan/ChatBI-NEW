from __future__ import annotations

import json
from collections.abc import Iterator

import orjson

from apps.chatbi.errors import DatasourceSelectionError
from apps.chatbi.models import (
    ChatRecord,
    DatasourceSelectionCandidate,
    DatasourceSelectionData,
    DatasourceSelectionEvent,
    ModelMessage,
)
from apps.chatbi.services.conversation.chat_record_service import ChatRecordService
from apps.chatbi.services.generation.ports import (
    DatasourceSelectionPromptBuilder,
    GenerationModelClient,
)
from apps.chatbi.services.generation.streaming import (
    StreamAccumulator,
    stream_generation,
)


class DatasourceSelectionService:
    """统一数据源自动选择、模型选择、范围校验和记录绑定。"""

    def __init__(
        self,
        *,
        prompt_builder: DatasourceSelectionPromptBuilder,
        model_client: GenerationModelClient,
        chat_record_service: ChatRecordService,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._model_client = model_client
        self._chat_record_service = chat_record_service

    def prepare(
        self,
        data: DatasourceSelectionData,
    ) -> list[ModelMessage]:
        self._validate_data(data)
        if data.auto_select:
            return []
        messages = self._prompt_builder.build(data)
        self._validate_messages(messages)
        return messages

    def generate(
        self,
        data: DatasourceSelectionData,
        messages: list[ModelMessage] | None = None,
    ) -> Iterator[DatasourceSelectionEvent]:
        self._validate_data(data)
        if data.auto_select:
            yield DatasourceSelectionEvent(
                kind="completed",
                selected_datasource_id=data.candidates[0].id,
                model_used=False,
            )
            return

        prepared_messages = self.prepare(data) if messages is None else messages
        self._validate_messages(prepared_messages)
        stream = StreamAccumulator()
        for chunk in stream_generation(prepared_messages, self._model_client, stream):
            yield DatasourceSelectionEvent(
                kind="chunk",
                content=chunk.content,
                reasoning_content=chunk.reasoning_content,
                model_used=True,
                token_usage=dict(stream.token_usage),
            )

        try:
            selected_id = _parse_selected_datasource(
                stream.content,
                candidates=data.candidates,
            )
        except DatasourceSelectionError as exc:
            yield DatasourceSelectionEvent(
                kind="completed",
                content=stream.content,
                reasoning_content=stream.reasoning_content,
                model_used=True,
                error=str(exc),
                token_usage=stream.token_usage,
            )
            return
        yield DatasourceSelectionEvent(
            kind="completed",
            content=stream.content,
            reasoning_content=stream.reasoning_content,
            selected_datasource_id=selected_id,
            model_used=True,
            token_usage=stream.token_usage,
        )

    def bind_selection(
        self,
        data: DatasourceSelectionData,
        event: DatasourceSelectionEvent,
        *,
        record_engine_type: str,
        conversation_engine_type: str,
    ) -> ChatRecord:
        """把已解析并完成连接验证的数据源同时绑定到会话和记录。"""

        selected_id = event.selected_datasource_id
        if event.kind != "completed" or selected_id is None or event.error:
            raise DatasourceSelectionError("DATASOURCE_SELECTION_RESULT_REQUIRED")
        candidate_ids = {candidate.id for candidate in data.candidates}
        if selected_id not in candidate_ids:
            raise DatasourceSelectionError(
                f"DATASOURCE_SELECTION_OUT_OF_SCOPE:{selected_id}"
            )
        answer = (
            orjson.dumps({"content": event.content}).decode()
            if event.model_used
            else None
        )
        return self._chat_record_service.bind_datasource_selection_by_id(
            data.record_id,
            datasource_id=selected_id,
            record_engine_type=record_engine_type,
            conversation_engine_type=conversation_engine_type,
            answer=answer,
        )

    @staticmethod
    def _validate_data(data: DatasourceSelectionData) -> None:
        if data.record_id <= 0:
            raise DatasourceSelectionError("DATASOURCE_SELECTION_RECORD_ID_INVALID")
        if not data.candidates:
            raise DatasourceSelectionError(
                "No available datasource configuration found"
            )
        candidate_ids: set[int] = set()
        for candidate in data.candidates:
            if (
                not isinstance(candidate.id, int)
                or isinstance(candidate.id, bool)
                or candidate.id <= 0
                or not isinstance(candidate.name, str)
                or not candidate.name.strip()
                or (
                    candidate.description is not None
                    and not isinstance(candidate.description, str)
                )
            ):
                raise DatasourceSelectionError(
                    "DATASOURCE_SELECTION_CANDIDATE_INVALID"
                )
            if candidate.id in candidate_ids:
                raise DatasourceSelectionError(
                    f"DATASOURCE_SELECTION_CANDIDATE_DUPLICATED:{candidate.id}"
                )
            candidate_ids.add(candidate.id)
        if data.auto_select and len(data.candidates) != 1:
            raise DatasourceSelectionError(
                "DATASOURCE_SELECTION_AUTO_SELECT_REQUIRES_ONE_CANDIDATE"
            )

    @staticmethod
    def _validate_messages(messages: list[ModelMessage]) -> None:
        if not messages or any(not message.content.strip() for message in messages):
            raise DatasourceSelectionError("DATASOURCE_SELECTION_PROMPT_INVALID")


def _parse_selected_datasource(
    content: str,
    *,
    candidates: list[DatasourceSelectionCandidate],
) -> int:
    decoder = json.JSONDecoder()
    payload: object | None = None
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            candidate_payload, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate_payload, dict):
            payload = candidate_payload
            break
    if not isinstance(payload, dict):
        raise DatasourceSelectionError(
            f"Cannot parse datasource from answer: {content}"
        )

    selected_id = payload.get("id")
    if (
        isinstance(selected_id, int)
        and not isinstance(selected_id, bool)
        and selected_id > 0
    ):
        candidate_ids = {candidate.id for candidate in candidates}
        if selected_id not in candidate_ids:
            raise DatasourceSelectionError(
                f"DATASOURCE_SELECTION_OUT_OF_SCOPE:{selected_id}"
            )
        return selected_id

    failure = payload.get("fail")
    if isinstance(failure, str) and failure.strip():
        raise DatasourceSelectionError(failure.strip())
    raise DatasourceSelectionError("No available datasource configuration found")


__all__ = ["DatasourceSelectionError", "DatasourceSelectionService"]
