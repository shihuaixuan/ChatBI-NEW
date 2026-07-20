from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlmodel import Session

from apps.ai_model.streaming import process_stream
from apps.chatbi.chat_record import build_chat_record_service
from apps.chatbi.models import (
    AnalysisPredictionGenerationData,
    AnalysisPredictionMessage,
    AnalysisPredictionModelChunk,
    ChatRecordAuxiliaryType,
)
from apps.chatbi.services import AnalysisPredictionService
from apps.template.generate_analysis.generator import get_analysis_template
from apps.template.generate_predict.generator import get_predict_template


class TemplateAnalysisPredictionPromptBuilder:
    """使用现有分析和预测模板生成稳定消息。"""

    def build(
        self,
        data: AnalysisPredictionGenerationData,
    ) -> list[AnalysisPredictionMessage]:
        if data.generation_type is ChatRecordAuxiliaryType.ANALYSIS:
            template_loader = cast(
                Callable[[], dict[str, str]],
                get_analysis_template,
            )
            template = template_loader()
            system_content = template["system"].format(
                lang=data.language,
                terminologies=data.terminologies,
                custom_prompt=data.custom_prompt,
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
                custom_prompt=data.custom_prompt,
                sqlbot_name=data.assistant_name,
            )
        return [
            AnalysisPredictionMessage(role="system", content=system_content),
            AnalysisPredictionMessage(
                role="human",
                content=template["user"].format(
                    fields=data.fields,
                    data=data.data,
                ),
            ),
        ]


class LangChainAnalysisPredictionModelClient:
    """把分析预测消息 DTO 适配到现有 LangChain 流式模型。"""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def stream(
        self,
        messages: list[AnalysisPredictionMessage],
    ) -> Iterator[AnalysisPredictionModelChunk]:
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
            yield AnalysisPredictionModelChunk(
                content=chunk["content"],
                reasoning_content=chunk["reasoning_content"],
                token_usage=dict(token_usage),
            )


def build_analysis_prediction_service(
    session: Session,
    llm: BaseChatModel,
) -> AnalysisPredictionService:
    """装配分析和预测生成所需的外层实现。"""

    return AnalysisPredictionService(
        prompt_builder=TemplateAnalysisPredictionPromptBuilder(),
        model_client=LangChainAnalysisPredictionModelClient(llm),
        chat_record_service=build_chat_record_service(session),
    )


__all__ = [
    "LangChainAnalysisPredictionModelClient",
    "TemplateAnalysisPredictionPromptBuilder",
    "build_analysis_prediction_service",
]
