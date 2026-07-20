from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

from langchain.chat_models.base import BaseChatModel

from apps.chatbi.adapters.sql_generation import LangChainSQLGenerationModelClient
from apps.chatbi.models import DynamicSQLGenerationData, SQLGenerationMessage
from apps.chatbi.services import DynamicSQLGenerationService
from apps.template.generate_dynamic.generator import get_dynamic_template


class TemplateDynamicSQLGenerationPromptBuilder:
    """使用现有动态 SQL 模板生成稳定消息。"""

    def build(
        self,
        data: DynamicSQLGenerationData,
    ) -> list[SQLGenerationMessage]:
        template_loader = cast(
            Callable[[], dict[str, str]],
            get_dynamic_template,
        )
        template = template_loader()
        subqueries = [
            {"table": mapping.table, "query": mapping.query}
            for mapping in data.subqueries
        ]
        return [
            SQLGenerationMessage(
                role="system",
                content=template["system"].format(
                    lang=data.language,
                    engine=data.engine,
                    sqlbot_name=data.assistant_name,
                ),
                system_context=True,
            ),
            SQLGenerationMessage(
                role="human",
                content=template["user"].format(
                    sql=data.sql,
                    sub_query=json.dumps(subqueries, ensure_ascii=False),
                ),
            ),
        ]


def build_dynamic_sql_generation_service(
    llm: BaseChatModel,
) -> DynamicSQLGenerationService:
    """装配动态 SQL 生成所需的外层实现。"""

    return DynamicSQLGenerationService(
        prompt_builder=TemplateDynamicSQLGenerationPromptBuilder(),
        model_client=LangChainSQLGenerationModelClient(llm),
    )


__all__ = [
    "TemplateDynamicSQLGenerationPromptBuilder",
    "build_dynamic_sql_generation_service",
]
