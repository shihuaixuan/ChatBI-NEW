from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

import orjson
from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlmodel import Session

from apps.ai_model.streaming import process_stream
from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.models import (
    DatasourceSelectionData,
    DatasourceSelectionMessage,
    DatasourceSelectionModelChunk,
)
from apps.chatbi.services import DatasourceSelectionService
from apps.template.select_datasource.generator import get_datasource_template


class TemplateDatasourceSelectionPromptBuilder:
    """使用现有数据源选择模板生成稳定消息。"""

    def build(
        self,
        data: DatasourceSelectionData,
    ) -> list[DatasourceSelectionMessage]:
        template_loader = cast(
            Callable[[], dict[str, str]],
            get_datasource_template,
        )
        template = template_loader()
        candidates = [
            {
                "id": candidate.id,
                "name": candidate.name,
                "description": candidate.description,
            }
            for candidate in data.candidates
        ]
        return [
            DatasourceSelectionMessage(
                role="system",
                content=template["system"].format(
                    lang=data.language,
                    sqlbot_name=data.assistant_name,
                ),
            ),
            DatasourceSelectionMessage(
                role="human",
                content=template["user"].format(
                    lang=data.language,
                    question=data.question,
                    data=orjson.dumps(candidates).decode(),
                ),
            ),
        ]


class LangChainDatasourceSelectionModelClient:
    """把数据源选择消息 DTO 适配到现有 LangChain 流式模型。"""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def stream(
        self,
        messages: list[DatasourceSelectionMessage],
    ) -> Iterator[DatasourceSelectionModelChunk]:
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
            yield DatasourceSelectionModelChunk(
                content=chunk["content"],
                reasoning_content=chunk["reasoning_content"],
                token_usage=dict(token_usage),
            )


def build_datasource_selection_service(
    session: Session,
    llm: BaseChatModel,
) -> DatasourceSelectionService:
    """装配数据源选择所需的外层实现。"""

    return DatasourceSelectionService(
        prompt_builder=TemplateDatasourceSelectionPromptBuilder(),
        model_client=LangChainDatasourceSelectionModelClient(llm),
        chat_record_service=build_chat_record_service(session),
    )


__all__ = [
    "LangChainDatasourceSelectionModelClient",
    "TemplateDatasourceSelectionPromptBuilder",
    "build_datasource_selection_service",
]
