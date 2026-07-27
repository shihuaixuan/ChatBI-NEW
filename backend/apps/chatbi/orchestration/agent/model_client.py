"""Agent 默认模型客户端。"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from apps.ai_model.model_factory import LLMFactory, get_default_config


class DefaultAgentModelClient:
    """使用系统默认模型配置，并在首次调用时惰性创建模型。"""

    def __init__(self) -> None:
        self._llm: BaseChatModel | None = None

    def invoke(
        self,
        messages: list[BaseMessage],
        tool_specs: list[dict[str, Any]],
    ) -> AIMessage:
        response = self._get_llm().bind_tools(tool_specs).invoke(messages)
        if not isinstance(response, AIMessage):
            raise TypeError("AGENT_MODEL_RESPONSE_NOT_AI_MESSAGE")
        return response

    def _get_llm(self) -> BaseChatModel:
        if self._llm is None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                config = asyncio.run(get_default_config())
            else:
                raise RuntimeError(
                    "agent model cannot be loaded inside a running event loop"
                )
            self._llm = LLMFactory.create_llm(config).llm
        return self._llm


__all__ = ["DefaultAgentModelClient"]
