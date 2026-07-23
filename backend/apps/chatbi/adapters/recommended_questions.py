from __future__ import annotations

from collections.abc import Callable
from typing import cast

import orjson
from langchain.chat_models.base import BaseChatModel
from sqlmodel import Session

from apps.chatbi.adapters.langchain import LangChainGenerationModelClient
from apps.chatbi.adapters.prompts import get_guess_question_template
from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.models import (
    ModelMessage,
    RecommendedQuestionGenerationData,
)
from apps.chatbi.repository.sqlmodel import (
    SQLModelRecommendedQuestionHistoryRepository,
)
from apps.chatbi.services.generation import RecommendedQuestionService


class TemplateRecommendedQuestionPromptBuilder:
    """使用现有模板生成推荐问题消息。"""

    def build(
        self,
        data: RecommendedQuestionGenerationData,
        old_questions: list[str],
    ) -> list[ModelMessage]:
        template_loader = cast(
            Callable[[], dict[str, str]],
            get_guess_question_template,
        )
        template = template_loader()
        return [
            ModelMessage(
                role="system",
                content=template["system"].format(
                    lang=data.language,
                    articles_number=data.articles_number,
                    sqlbot_name=data.assistant_name,
                ),
            ),
            ModelMessage(
                role="human",
                content=template["user"].format(
                    question=data.question,
                    schema=data.schema,
                    old_questions=orjson.dumps(old_questions).decode(),
                ),
            ),
        ]


def build_recommended_question_service(
    session: Session,
    llm: BaseChatModel,
) -> RecommendedQuestionService:
    """装配推荐问题生成所需的外层实现。"""

    return RecommendedQuestionService(
        history_provider=SQLModelRecommendedQuestionHistoryRepository(session),
        prompt_builder=TemplateRecommendedQuestionPromptBuilder(),
        model_client=LangChainGenerationModelClient(llm),
        chat_record_service=build_chat_record_service(session),
    )


__all__ = [
    "TemplateRecommendedQuestionPromptBuilder",
    "build_recommended_question_service",
]
