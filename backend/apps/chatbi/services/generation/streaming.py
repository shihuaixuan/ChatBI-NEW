"""生成能力共享的流消费骨架（R2：唯一的累计实现）。

只统一"流消费与累计"这一无业务语义的部分；提示词构造、解析规则、事件信封、
错误分类仍由各能力自持（目标设计 §4.3 防过度抽象边界）。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from apps.chatbi.models.dto.streaming import ModelMessage, ModelStreamChunk
from apps.chatbi.services.generation.ports import GenerationModelClient


@dataclass
class StreamAccumulator:
    """模型流的累计状态：完整正文、完整思考内容与累计 token 用量。"""

    content: str = ""
    reasoning_content: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)


def stream_generation(
    messages: list[ModelMessage],
    client: GenerationModelClient,
    accumulator: StreamAccumulator,
) -> Iterator[ModelStreamChunk]:
    """消费模型流并累计到 accumulator，逐块转发给调用方构造事件。"""

    for chunk in client.stream(messages):
        accumulator.content += chunk.content
        accumulator.reasoning_content += chunk.reasoning_content
        accumulator.token_usage.update(chunk.token_usage)
        yield chunk


def ensure_prompt_messages(
    messages: list[ModelMessage],
    error: Exception,
) -> None:
    """提示词消息为空或存在空白正文时抛出能力自身的错误类型。"""

    if not messages or any(not message.content.strip() for message in messages):
        raise error


__all__ = ["StreamAccumulator", "ensure_prompt_messages", "stream_generation"]
