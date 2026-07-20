from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

import orjson
from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlmodel import Session

from apps.ai_model.streaming import process_stream
from apps.chatbi.chat_record import build_chat_record_service
from apps.chatbi.models import (
    RecommendedQuestionGenerationData,
    RecommendedQuestionMessage,
    RecommendedQuestionModelChunk,
)
from apps.chatbi.repository.sqlmodel import (
    SQLModelRecommendedQuestionHistoryProvider,
)
from apps.chatbi.services import RecommendedQuestionService
from apps.template.generate_guess_question.generator import (
    get_guess_question_template,
)


class TemplateRecommendedQuestionPromptBuilder:
    """使用现有模板生成推荐问题消息。"""

    def build(
        self,
        data: RecommendedQuestionGenerationData,
        old_questions: list[str],
    ) -> list[RecommendedQuestionMessage]:
        template_loader = cast(
            Callable[[], dict[str, str]],
            get_guess_question_template,
        )
        template = template_loader()
        return [
            RecommendedQuestionMessage(
                role="system",
                content=template["system"].format(
                    lang=data.language,
                    articles_number=data.articles_number,
                    sqlbot_name=data.assistant_name,
                ),
            ),
            RecommendedQuestionMessage(
                role="human",
                content=template["user"].format(
                    question=data.question,
                    schema=data.schema,
                    old_questions=orjson.dumps(old_questions).decode(),
                ),
            ),
        ]


class LangChainRecommendedQuestionModelClient:
    """把稳定消息 DTO 适配到现有 LangChain 流式模型。"""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def stream(
        self,
        messages: list[RecommendedQuestionMessage],
    ) -> Iterator[RecommendedQuestionModelChunk]:
        langchain_messages: list[BaseMessage] = []
        for message in messages:
            if message.role == "system":
                langchain_messages.append(SystemMessage(content=message.content))
            elif message.role == "human":
                langchain_messages.append(HumanMessage(content=message.content))
            else:
                langchain_messages.append(AIMessage(content=message.content))

        token_usage: dict[str, int] = {}
        for chunk in process_stream(
            self._llm.stream(langchain_messages),
            token_usage,
        ):
            yield RecommendedQuestionModelChunk(
                content=chunk["content"],
                reasoning_content=chunk["reasoning_content"],
                token_usage=dict(token_usage),
            )


def build_recommended_question_service(
    session: Session,
    llm: BaseChatModel,
) -> RecommendedQuestionService:
    """装配推荐问题生成所需的外层实现。"""

    return RecommendedQuestionService(
        history_provider=SQLModelRecommendedQuestionHistoryProvider(session),
        prompt_builder=TemplateRecommendedQuestionPromptBuilder(),
        model_client=LangChainRecommendedQuestionModelClient(llm),
        chat_record_service=build_chat_record_service(session),
    )


__all__ = [
    "LangChainRecommendedQuestionModelClient",
    "TemplateRecommendedQuestionPromptBuilder",
    "build_recommended_question_service",
]
