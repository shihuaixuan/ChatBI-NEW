from __future__ import annotations

from collections.abc import Callable
from typing import cast

import orjson
from langchain.chat_models.base import BaseChatModel
from sqlmodel import Session

from apps.chatbi.adapters.langchain import LangChainGenerationModelClient
from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.models import (
    RecommendedQuestionGenerationData,
    RecommendedQuestionMessage,
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


# 共享客户端（R2 合并）；旧名保留（台账 E2）。
LangChainRecommendedQuestionModelClient = LangChainGenerationModelClient


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
