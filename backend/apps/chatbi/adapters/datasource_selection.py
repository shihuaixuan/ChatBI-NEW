from __future__ import annotations

from collections.abc import Callable
from typing import cast

import orjson
from langchain.chat_models.base import BaseChatModel
from sqlmodel import Session

from apps.chatbi.adapters.langchain import LangChainGenerationModelClient
from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.models import (
    DatasourceSelectionData,
    DatasourceSelectionMessage,
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


# 共享客户端（R2 合并）；旧名保留（台账 E2）。
LangChainDatasourceSelectionModelClient = LangChainGenerationModelClient


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
