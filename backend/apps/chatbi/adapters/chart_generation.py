from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlmodel import Session

from apps.ai_model.streaming import process_stream
from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.models import (
    ChartGenerationData,
    ChartGenerationMessage,
    ChartGenerationModelChunk,
)
from apps.chatbi.services import ChartGenerationService
from apps.template.generate_chart.generator import get_chart_template


class TemplateChartGenerationPromptBuilder:
    """使用现有图表模板和历史消息生成稳定消息。"""

    def build(
        self,
        data: ChartGenerationData,
    ) -> list[ChartGenerationMessage]:
        template_loader = cast(
            Callable[[], dict[str, str]],
            get_chart_template,
        )
        template = template_loader()
        messages = [
            ChartGenerationMessage(
                role="system",
                content=template["system"].format(
                    lang=data.language,
                    sqlbot_name=data.assistant_name,
                ),
                system_context=True,
            ),
            ChartGenerationMessage(
                role="human",
                content=template["generate_rules"].format(lang=data.language),
                system_context=True,
            ),
            ChartGenerationMessage(
                role="ai",
                content="我已掌握所有规则，我会严格遵守这些规则来生成符合要求的JSON。",
                system_context=True,
            ),
            *data.history,
            ChartGenerationMessage(
                role="human",
                content=template["user"].format(
                    lang=data.language,
                    sql=data.sql,
                    question=data.question,
                    rule=data.rule,
                    chart_type=data.chart_type,
                    schema=data.schema,
                ),
            ),
        ]
        return messages


class LangChainChartGenerationModelClient:
    """把图表消息 DTO 适配到现有 LangChain 流式模型。"""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def stream(
        self,
        messages: list[ChartGenerationMessage],
    ) -> Iterator[ChartGenerationModelChunk]:
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
            yield ChartGenerationModelChunk(
                content=chunk["content"],
                reasoning_content=chunk["reasoning_content"],
                token_usage=dict(token_usage),
            )


def build_chart_generation_service(
    session: Session,
    llm: BaseChatModel,
) -> ChartGenerationService:
    """装配图表生成所需的外层实现。"""

    return ChartGenerationService(
        prompt_builder=TemplateChartGenerationPromptBuilder(),
        model_client=LangChainChartGenerationModelClient(llm),
        chat_record_service=build_chat_record_service(session),
    )


__all__ = [
    "LangChainChartGenerationModelClient",
    "TemplateChartGenerationPromptBuilder",
    "build_chart_generation_service",
]
