from __future__ import annotations

from collections.abc import Iterator

import orjson

from apps.chatbi.models import (
    AnalysisPredictionGenerationData,
    AnalysisPredictionGenerationEvent,
    AnalysisPredictionMessage,
    ChatRecordAuxiliaryProjection,
    ChatRecordAuxiliaryType,
)
from apps.chatbi.services.conversation.chat_record_service import ChatRecordService
from apps.chatbi.services.generation.ports import (
    AnalysisPredictionPromptBuilder,
    GenerationModelClient,
)
from apps.chatbi.services.generation.streaming import (
    StreamAccumulator,
    stream_generation,
)


class AnalysisPredictionService:
    """统一分析和预测的提示词输入、模型流与结果投影。"""

    def __init__(
        self,
        *,
        prompt_builder: AnalysisPredictionPromptBuilder,
        model_client: GenerationModelClient,
        chat_record_service: ChatRecordService,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._model_client = model_client
        self._chat_record_service = chat_record_service

    def prepare(
        self,
        data: AnalysisPredictionGenerationData,
    ) -> list[AnalysisPredictionMessage]:
        self._validate_data(data)
        messages = self._prompt_builder.build(data)
        self._validate_messages(messages)
        return messages

    def generate(
        self,
        data: AnalysisPredictionGenerationData,
        messages: list[AnalysisPredictionMessage] | None = None,
    ) -> Iterator[AnalysisPredictionGenerationEvent]:
        self._validate_data(data)
        prepared_messages = self.prepare(data) if messages is None else messages
        self._validate_messages(prepared_messages)
        stream = StreamAccumulator()
        for chunk in stream_generation(prepared_messages, self._model_client, stream):
            yield AnalysisPredictionGenerationEvent(
                kind="chunk",
                content=chunk.content,
                reasoning_content=chunk.reasoning_content,
                token_usage=dict(stream.token_usage),
            )

        answer = orjson.dumps({"content": stream.content}).decode()
        if data.generation_type is ChatRecordAuxiliaryType.ANALYSIS:
            projection = ChatRecordAuxiliaryProjection(analysis=answer)
        else:
            projection = ChatRecordAuxiliaryProjection(predict=answer)
        self._chat_record_service.project_auxiliary_by_id(
            data.record_id,
            projection,
        )
        yield AnalysisPredictionGenerationEvent(
            kind="completed",
            content=stream.content,
            reasoning_content=stream.reasoning_content,
            token_usage=stream.token_usage,
        )

    @staticmethod
    def _validate_data(data: AnalysisPredictionGenerationData) -> None:
        if data.record_id <= 0:
            raise ValueError("ANALYSIS_PREDICTION_RECORD_ID_INVALID")
        if not isinstance(data.generation_type, ChatRecordAuxiliaryType):
            raise ValueError("ANALYSIS_PREDICTION_TYPE_INVALID")

    @staticmethod
    def _validate_messages(messages: list[AnalysisPredictionMessage]) -> None:
        if not messages or any(not message.content.strip() for message in messages):
            raise ValueError("ANALYSIS_PREDICTION_PROMPT_INVALID")


__all__ = ["AnalysisPredictionPromptBuilder", "AnalysisPredictionService"]
