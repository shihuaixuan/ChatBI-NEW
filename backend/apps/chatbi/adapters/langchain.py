"""生成能力共享的 LangChain 流式模型客户端（实现 generation.ports.GenerationModelClient）。"""

from __future__ import annotations

from collections.abc import Iterator

from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from apps.ai_model.streaming import process_stream
from apps.chatbi.models.dto.streaming import ModelMessage, ModelStreamChunk


class LangChainGenerationModelClient:
    """把共享消息 DTO 适配到现有 LangChain 流式模型。"""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def stream(
        self,
        messages: list[ModelMessage],
    ) -> Iterator[ModelStreamChunk]:
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
            yield ModelStreamChunk(
                content=chunk["content"],
                reasoning_content=chunk["reasoning_content"],
                token_usage=dict(token_usage),
            )


__all__ = ["LangChainGenerationModelClient"]
