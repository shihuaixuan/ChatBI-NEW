"""Agent 默认模型客户端。"""

from __future__ import annotations

import asyncio

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from apps.ai_model.model_factory import LLMFactory, get_default_config
from apps.chatbi.orchestration.agent.messages import (
    AgentMessage,
    AgentMessageRole,
    ModelDecision,
)
from apps.tool import ToolCall, ToolDefinition
from apps.tool.adapters import to_openai_tool_specs


class DefaultAgentModelClient:
    """使用系统默认模型配置，并在首次调用时惰性创建模型。"""

    def __init__(self) -> None:
        self._llm: BaseChatModel | None = None

    def invoke(
        self,
        messages: list[AgentMessage],
        tool_definitions: list[ToolDefinition],
    ) -> ModelDecision:
        tool_specs = to_openai_tool_specs(tool_definitions)
        response = self._get_llm().bind_tools(tool_specs).invoke(
            [_to_langchain_message(message) for message in messages]
        )
        if not isinstance(response, AIMessage):
            raise TypeError("AGENT_MODEL_RESPONSE_NOT_AI_MESSAGE")
        tool_calls = [
            ToolCall(
                name=str(call.get("name") or ""),
                args=dict(call.get("args") or {}),
                call_id=str(call.get("id") or ""),
            )
            for call in response.tool_calls
        ]
        usage = dict(response.usage_metadata or {})
        message = AgentMessage.assistant(
            _content_text(response),
            tool_calls=tool_calls,
            reasoning_content=str(
                response.additional_kwargs.get("reasoning_content") or ""
            )
            or None,
            usage=usage,
        )
        return ModelDecision(message=message, tool_calls=tool_calls, usage=usage)

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


def _to_langchain_message(message: AgentMessage) -> BaseMessage:
    """项目消息只在模型适配器中转换成 LangChain 消息。"""

    if message.role == AgentMessageRole.SYSTEM:
        return SystemMessage(content=message.content)
    if message.role == AgentMessageRole.USER:
        return HumanMessage(content=message.content)
    if message.role == AgentMessageRole.TOOL:
        if not message.tool_call_id:
            raise ValueError("AGENT_TOOL_MESSAGE_CALL_ID_MISSING")
        return ToolMessage(content=message.content, tool_call_id=message.tool_call_id)
    return AIMessage(
        content=message.content,
        additional_kwargs=(
            {"reasoning_content": message.reasoning_content}
            if message.reasoning_content
            else {}
        ),
        tool_calls=[
            {
                "name": call.name,
                "args": call.args,
                "id": call.call_id,
                "type": "tool_call",
            }
            for call in message.tool_calls
        ],
    )


def _content_text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content.strip()
    return "".join(
        str(part.get("text") or "")
        for part in message.content
        if isinstance(part, dict)
    ).strip()


__all__ = ["DefaultAgentModelClient"]
