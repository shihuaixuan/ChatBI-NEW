"""ChatBI 查询接口的 SSE 协议工具。

只负责事件编码与提示词日志投影，不进入业务与持久化边界。
"""

from collections.abc import Iterable
from typing import Any, Protocol

import orjson


class RolePromptMessage(Protocol):
    role: str
    content: str


def encode_sse_event(event_type: str, **payload: Any) -> str:
    """按现有 /chat 契约编码单个 SSE 事件。"""

    return "data:" + orjson.dumps({"type": event_type, **payload}).decode() + "\n\n"


def build_role_prompt_log(
    messages: Iterable[RolePromptMessage],
    assistant_content: str | None = None,
) -> list[dict[str, Any]]:
    """把按角色区分系统消息的提示词转换为步骤日志结构。"""

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


__all__ = [
    "build_role_prompt_log",
    "encode_sse_event",
]
