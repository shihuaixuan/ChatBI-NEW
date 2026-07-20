"""生成能力共享的流式模型消息与分块 DTO（R2 统一，取代 5 套同构定义）。"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """提示词稳定消息：role + 正文 + 是否属于系统上下文（用于历史日志过滤）。"""

    role: Literal["system", "human", "ai"]
    content: str
    system_context: bool = False


@dataclass(frozen=True, slots=True)
class ModelStreamChunk:
    """模型流式输出分块：增量正文、思考内容与累计 token 用量。"""

    content: str = ""
    reasoning_content: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)


__all__ = ["ModelMessage", "ModelStreamChunk"]
