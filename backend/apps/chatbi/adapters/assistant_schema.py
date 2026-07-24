"""助手外部数据源 Schema 适配。

仅服务仍需外部助手 Schema 的推荐问题生成路径，不承载会话主流程。
"""

from __future__ import annotations

from typing import Any, cast

from apps.assistant.public import AssistantOutDsFactory
from apps.chatbi.services.generation.context.scope import (
    DYNAMIC_DATASOURCE_ASSISTANT_TYPES,
)


def is_dynamic_assistant(current_assistant: Any | None) -> bool:
    """判断助手是否使用外部动态数据源。"""

    return (
        current_assistant is not None
        and getattr(current_assistant, "type", None) in DYNAMIC_DATASOURCE_ASSISTANT_TYPES
    )


def load_assistant_schema(
    current_assistant: Any,
    *,
    datasource_id: int,
    question: str,
    embedding: bool = False,
    table_names: list[str] | None = None,
) -> str:
    """从外部助手目录读取 Schema 文本。"""

    catalog = AssistantOutDsFactory.get_instance(current_assistant)
    return cast(
        str,
        catalog.get_db_schema(
            datasource_id,
            question,
            embedding=embedding,
            table_list=cast(list[str], table_names),
        ),
    )


__all__ = [
    "is_dynamic_assistant",
    "load_assistant_schema",
]
