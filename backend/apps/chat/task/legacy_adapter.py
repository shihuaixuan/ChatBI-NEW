from collections.abc import Iterable
from typing import Any, Protocol

import orjson


class RolePromptMessage(Protocol):
    role: str
    content: str


class ContextPromptMessage(RolePromptMessage, Protocol):
    system_context: bool


def encode_sse_event(event_type: str, **payload: Any) -> str:
    """按旧 Chat 协议编码单个 SSE 事件。"""

    return "data:" + orjson.dumps({"type": event_type, **payload}).decode() + "\n\n"


def build_role_prompt_log(
    messages: Iterable[RolePromptMessage],
    assistant_content: str | None = None,
) -> list[dict[str, Any]]:
    """把按角色区分系统消息的提示词转换为旧日志结构。"""

    result = [
        {
            "type": message.role,
            "sqlbot_system": message.role == "system",
            "content": message.content,
        }
        for message in messages
    ]
    if assistant_content is not None:
        result.append(
            {
                "type": "ai",
                "sqlbot_system": False,
                "content": assistant_content,
            }
        )
    return result


def build_context_prompt_log(
    messages: Iterable[ContextPromptMessage],
    assistant_content: str | None = None,
) -> list[dict[str, Any]]:
    """把带显式系统上下文标记的提示词转换为旧日志结构。"""

    result = [
        {
            "type": message.role,
            "sqlbot_system": message.system_context,
            "content": message.content,
        }
        for message in messages
    ]
    if assistant_content is not None:
        result.append(
            {
                "type": "ai",
                "sqlbot_system": False,
                "content": assistant_content,
            }
        )
    return result


__all__ = [
    "build_context_prompt_log",
    "build_role_prompt_log",
    "encode_sse_event",
]
