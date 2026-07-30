"""Function Calling ReAct 的单轮推理。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.messages import (
    AgentMessage,
    ModelDecision,
    fold_tool_messages,
)
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tool_visibility import visible_tool_names
from apps.tool import ToolCall, ToolDefinition, ToolRegistry
from apps.trace import AgentTracer, llm_attributes


class AgentModelClient(Protocol):
    """Reasoner 依赖的最小模型调用接口。"""

    def invoke(
        self,
        messages: list[AgentMessage],
        tool_definitions: list[ToolDefinition],
    ) -> ModelDecision: ...


@dataclass(frozen=True)
class AgentDecision:
    """LLM 一轮输出的稳定决策结果。"""

    response: AgentMessage
    reasoning: str
    tool_calls: list[ToolCall]
    usage: dict[str, Any]

    @property
    def is_direct_answer(self) -> bool:
        return not self.tool_calls


class AgentReasoner:
    """构造推理输入、调用模型并解析 Function Calling 决策。"""

    def __init__(
        self,
        config: AgentConfig,
        model_client: AgentModelClient,
        registry: ToolRegistry,
        tracer: AgentTracer,
    ) -> None:
        self._config = config
        self._model_client = model_client
        self._registry = registry
        self._tracer = tracer

    def decide(self, state: AgentRuntimeState, mode: str) -> AgentDecision:
        """执行一轮 Reason，并把模型响应转换为结构化决策。"""

        if mode not in {"normal", "soft"}:
            raise ValueError(f"Unsupported reasoning mode: {mode}")
        fold_tool_messages(state.messages, self._config.context_fold_chars)
        invoke_messages = self._invoke_messages(state, mode)
        tool_definitions = self._tool_definitions(state, mode)
        with self._tracer.span(
            "chat",
            llm_attributes(model=self._model_client.__class__.__name__),
        ) as llm_span:
            model_decision = self._model_client.invoke(invoke_messages, tool_definitions)
            tool_calls = [
                self._registry.prepare_call(call, state.context)
                for call in model_decision.tool_calls
            ]
            response = model_decision.message.model_copy(
                update={"tool_calls": tool_calls}
            )
            usage = model_decision.usage
            for source, attribute in (
                ("input_tokens", "gen_ai.usage.input_tokens"),
                ("output_tokens", "gen_ai.usage.output_tokens"),
                ("total_tokens", "gen_ai.usage.total_tokens"),
            ):
                if usage.get(source) is not None:
                    llm_span.set_attribute(attribute, int(usage[source]))

        state.budget.record_llm_turn(usage)
        _record_tool_call_preparations(
            state,
            model_decision.tool_calls,
            tool_calls,
        )
        state.messages.append(response)
        return AgentDecision(
            response=response,
            reasoning=_content_text(response),
            tool_calls=tool_calls,
            usage=usage,
        )

    def _invoke_messages(
        self,
        state: AgentRuntimeState,
        mode: str,
    ) -> list[AgentMessage]:
        system = state.require_system()
        if mode == "soft":
            return [
                system,
                AgentMessage.user(
                    "<system-reminder>预算接近上限。请基于已有工具结果尽快 finish；"
                    "若关键歧义未消可 clarify；不要再启动新的检索或 SQL 探索。</system-reminder>"
                ),
                *state.messages,
            ]
        return [system, *state.messages]

    def _tool_definitions(
        self,
        state: AgentRuntimeState,
        mode: str,
    ) -> list[ToolDefinition]:
        allowed = self.available_tool_names(state, mode)
        return self._registry.definitions(allowed=allowed)

    def available_tool_names(
        self,
        state: AgentRuntimeState,
        mode: str,
    ) -> list[str]:
        return visible_tool_names(
            state,
            mode,
            self._registry.names(),
        )


def _content_text(message: AgentMessage) -> str:
    return message.content.strip() or str(message.reasoning_content or "").strip()


def _record_tool_call_preparations(
    state: AgentRuntimeState,
    original_calls: list[ToolCall],
    prepared_calls: list[ToolCall],
) -> None:
    """记录模型参数被可信工具计划调整的事实，供运行审计与问题定位。"""

    adjustments = [
        {
            "tool_call_id": original.call_id,
            "tool_name": original.name,
            "original_args": original.args,
            "prepared_args": prepared.args,
        }
        for original, prepared in zip(original_calls, prepared_calls, strict=True)
        if original.args != prepared.args
    ]
    if not adjustments:
        return
    history = state.context.state.setdefault("tool_call_preparations", [])
    if not isinstance(history, list):
        raise TypeError("AGENT_TOOL_CALL_PREPARATIONS_INVALID")
    history.extend(adjustments)
    del history[:-20]


__all__ = ["AgentDecision", "AgentModelClient", "AgentReasoner"]
