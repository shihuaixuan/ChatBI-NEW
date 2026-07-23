from __future__ import annotations

from collections.abc import Callable
from typing import cast

from langchain.chat_models.base import BaseChatModel
from sqlmodel import Session

from apps.chatbi.adapters.langchain import LangChainGenerationModelClient
from apps.chatbi.adapters.prompts import get_chart_template
from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.models import (
    ChartGenerationData,
    ModelMessage,
)
from apps.chatbi.services.generation import ChartGenerationService


class TemplateChartGenerationPromptBuilder:
    """使用现有图表模板和历史消息生成稳定消息。"""

    def build(
        self,
        data: ChartGenerationData,
    ) -> list[ModelMessage]:
        template_loader = cast(
            Callable[[], dict[str, str]],
            get_chart_template,
        )
        template = template_loader()
        messages = [
            ModelMessage(
                role="system",
                content=template["system"].format(
                    lang=data.language,
                    sqlbot_name=data.assistant_name,
                ),
                system_context=True,
            ),
            ModelMessage(
                role="human",
                content=template["generate_rules"].format(lang=data.language),
                system_context=True,
            ),
            ModelMessage(
                role="ai",
                content="我已掌握所有规则，我会严格遵守这些规则来生成符合要求的JSON。",
                system_context=True,
            ),
            *data.history,
            ModelMessage(
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


def build_chart_generation_service(
    session: Session,
    llm: BaseChatModel,
) -> ChartGenerationService:
    """装配图表生成所需的外层实现。"""

    return ChartGenerationService(
        prompt_builder=TemplateChartGenerationPromptBuilder(),
        model_client=LangChainGenerationModelClient(llm),
        chat_record_service=build_chat_record_service(session),
    )


__all__ = [
    "TemplateChartGenerationPromptBuilder",
    "build_chart_generation_service",
]
