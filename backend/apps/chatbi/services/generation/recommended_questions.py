from __future__ import annotations

import json
from collections.abc import Iterator

import orjson

from apps.chatbi.models import (
    ModelMessage,
    RecommendedQuestionGenerationData,
    RecommendedQuestionGenerationEvent,
)
from apps.chatbi.services.generation.ports import (
    GenerationModelClient,
    RecommendedQuestionHistoryRepository,
    RecommendedQuestionPromptBuilder,
)
from apps.chatbi.services.generation.streaming import (
    StreamAccumulator,
    ensure_prompt_messages,
    stream_generation,
)
from apps.conversation import ChatRecordService


class RecommendedQuestionService:
    """统一推荐问题的提示词输入、模型流、归一化和结果投影。"""

    def __init__(
        self,
        *,
        history_provider: RecommendedQuestionHistoryRepository,
        prompt_builder: RecommendedQuestionPromptBuilder,
        model_client: GenerationModelClient,
        chat_record_service: ChatRecordService,
    ) -> None:
        self._history_provider = history_provider
        self._prompt_builder = prompt_builder
        self._model_client = model_client
        self._chat_record_service = chat_record_service

    def prepare(
        self,
        data: RecommendedQuestionGenerationData,
    ) -> list[ModelMessage]:
        self._validate_data(data)
        old_questions = [
            question.strip()
            for question in self._history_provider.list_recent(
                data.datasource_id,
                limit=20,
            )
            if question.strip()
        ]
        messages = self._prompt_builder.build(data, old_questions)
        if not messages or any(not message.content.strip() for message in messages):
            raise ValueError("RECOMMENDED_QUESTION_PROMPT_INVALID")
        return messages

    def generate(
        self,
        data: RecommendedQuestionGenerationData,
        messages: list[ModelMessage] | None = None,
    ) -> Iterator[RecommendedQuestionGenerationEvent]:
        self._validate_data(data)
        prepared_messages = self.prepare(data) if messages is None else messages
        ensure_prompt_messages(
            prepared_messages,
            ValueError("RECOMMENDED_QUESTION_PROMPT_INVALID"),
        )
        stream = StreamAccumulator()
        for chunk in stream_generation(prepared_messages, self._model_client, stream):
            yield RecommendedQuestionGenerationEvent(
                kind="chunk",
                content=chunk.content,
                reasoning_content=chunk.reasoning_content,
                token_usage=dict(stream.token_usage),
            )

        questions = _normalize_questions(
            stream.content,
            limit=data.articles_number,
        )
        questions_json = orjson.dumps(questions).decode()
        self._chat_record_service.project_recommendation_by_id(
            data.record_id,
            answer=orjson.dumps({"content": stream.content}).decode(),
            questions=questions_json,
            articles_number=data.articles_number,
        )
        yield RecommendedQuestionGenerationEvent(
            kind="completed",
            content=stream.content,
            reasoning_content=stream.reasoning_content,
            recommended_question=questions_json,
            token_usage=stream.token_usage,
        )

    @staticmethod
    def _validate_data(data: RecommendedQuestionGenerationData) -> None:
        if data.record_id <= 0:
            raise ValueError("RECOMMENDED_QUESTION_RECORD_ID_INVALID")
        if not data.question.strip():
            raise ValueError("RECOMMENDED_QUESTION_REQUIRED")
        if data.articles_number <= 0:
            raise ValueError("RECOMMENDED_QUESTION_COUNT_INVALID")


def _normalize_questions(content: str, *, limit: int) -> list[str]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "[":
            continue
        try:
            payload, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, list):
            continue
        questions = [
            item.strip()
            for item in payload
            if isinstance(item, str) and item.strip()
        ]
        return questions[:limit]
    return []


__all__ = ["RecommendedQuestionService"]
