from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

from langchain.chat_models.base import BaseChatModel
from sqlmodel import Session

from apps.chatbi.chat_record import build_chat_record_service
from apps.chatbi.models import PermissionSQLGenerationData, SQLGenerationMessage
from apps.chatbi.services import PermissionSQLGenerationService
from apps.template.filter.generator import get_permissions_template
from infrastructure.sql_generation import LangChainSQLGenerationModelClient


class TemplatePermissionSQLGenerationPromptBuilder:
    """使用现有权限模板生成稳定消息。"""

    def build(
        self,
        data: PermissionSQLGenerationData,
    ) -> list[SQLGenerationMessage]:
        template_loader = cast(
            Callable[[], dict[str, str]],
            get_permissions_template,
        )
        template = template_loader()
        filters = [
            {"table": item.table, "filter": item.condition}
            for item in data.filters
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
                    filter=json.dumps(filters, ensure_ascii=False),
                ),
            ),
        ]


def build_permission_sql_generation_service(
    session: Session,
    llm: BaseChatModel,
) -> PermissionSQLGenerationService:
    """装配权限 SQL 生成所需的外层实现。"""

    return PermissionSQLGenerationService(
        prompt_builder=TemplatePermissionSQLGenerationPromptBuilder(),
        model_client=LangChainSQLGenerationModelClient(llm),
        chat_record_service=build_chat_record_service(session),
    )


__all__ = [
    "TemplatePermissionSQLGenerationPromptBuilder",
    "build_permission_sql_generation_service",
]
