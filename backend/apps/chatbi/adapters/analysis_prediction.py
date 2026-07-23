from __future__ import annotations

from collections.abc import Callable
from typing import cast

from langchain.chat_models.base import BaseChatModel
from sqlmodel import Session

from apps.chatbi.adapters.langchain import LangChainGenerationModelClient
from apps.chatbi.adapters.prompts import get_analysis_template, get_predict_template
from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.models import (
    AnalysisPredictionGenerationData,
    ChatRecordAuxiliaryType,
    ModelMessage,
)
from apps.chatbi.services.generation import AnalysisPredictionService


class TemplateAnalysisPredictionPromptBuilder:
    """使用现有分析和预测模板生成稳定消息。"""

    def build(
        self,
        data: AnalysisPredictionGenerationData,
    ) -> list[ModelMessage]:
        if data.generation_type is ChatRecordAuxiliaryType.ANALYSIS:
            template_loader = cast(
                Callable[[], dict[str, str]],
                get_analysis_template,
            )
            template = template_loader()
            system_content = template["system"].format(
                lang=data.language,
                terminologies=data.terminologies,
                sqlbot_name=data.assistant_name,
            )
        else:
            template_loader = cast(
                Callable[[], dict[str, str]],
                get_predict_template,
            )
            template = template_loader()
            system_content = template["system"].format(
                lang=data.language,
                sqlbot_name=data.assistant_name,
            )
        return [
            ModelMessage(role="system", content=system_content),
            ModelMessage(
                role="human",
                content=template["user"].format(
                    fields=data.fields,
                    data=data.data,
                ),
            ),
        ]


def build_analysis_prediction_service(
    session: Session,
    llm: BaseChatModel,
) -> AnalysisPredictionService:
    """装配分析和预测生成所需的外层实现。"""

    return AnalysisPredictionService(
        prompt_builder=TemplateAnalysisPredictionPromptBuilder(),
        model_client=LangChainGenerationModelClient(llm),
        chat_record_service=build_chat_record_service(session),
    )


__all__ = [
    "TemplateAnalysisPredictionPromptBuilder",
    "build_analysis_prediction_service",
]
