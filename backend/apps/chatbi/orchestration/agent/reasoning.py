"""Function Calling ReAct 的单轮推理。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.tool import ToolCallRequest, ToolRegistry, fold_tool_messages
from apps.trace import AgentTracer, llm_attributes


class AgentModelClient(Protocol):
    """Reasoner 依赖的最小模型调用接口。"""

    def invoke(
        self,
        messages: list[BaseMessage],
        tool_specs: list[dict[str, Any]],
    ) -> AIMessage: ...


@dataclass(frozen=True)
class AgentDecision:
    """LLM 一轮输出的稳定决策结果。"""

    response: AIMessage
    reasoning: str
    tool_calls: list[ToolCallRequest]
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
        tool_specs = self._tool_specs(state, mode)
        with self._tracer.span(
            "chat",
            llm_attributes(model=self._model_client.__class__.__name__),
        ) as llm_span:
            response = self._model_client.invoke(invoke_messages, tool_specs)
            usage = dict(getattr(response, "usage_metadata", None) or {})
            for source, attribute in (
                ("input_tokens", "gen_ai.usage.input_tokens"),
                ("output_tokens", "gen_ai.usage.output_tokens"),
                ("total_tokens", "gen_ai.usage.total_tokens"),
            ):
                if usage.get(source) is not None:
                    llm_span.set_attribute(attribute, int(usage[source]))

        state.budget.record_llm_turn(usage)
        state.messages.append(response)
        return AgentDecision(
            response=response,
            reasoning=_content_text(response),
            tool_calls=[
                ToolCallRequest(
                    name=call.get("name") or "",
                    args=call.get("args") or {},
                    call_id=call.get("id") or "",
                )
                for call in list(getattr(response, "tool_calls", None) or [])
            ],
            usage=usage,
        )

    def _invoke_messages(
        self,
        state: AgentRuntimeState,
        mode: str,
    ) -> list[BaseMessage]:
        system = state.require_system()
        if mode == "soft":
            return [
                system,
                HumanMessage(
                    content=(
                        "<system-reminder>预算接近上限。请基于已有工具结果尽快 finish；"
                        "若关键歧义未消可 clarify；不要再启动新的检索或 SQL 探索。</system-reminder>"
                    )
                ),
                *state.messages,
            ]
        return [system, *state.messages]

    def _tool_specs(
        self,
        state: AgentRuntimeState,
        mode: str,
    ) -> list[dict[str, Any]]:
        if mode == "soft":
            allowed = state.budget.soft_tool_allowlist(self._registry.names())
            if allowed:
                return self._registry.tool_specs(allowed=allowed)
        return self._registry.tool_specs()


def _content_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
        ).strip()
    return ""


__all__ = ["AgentDecision", "AgentModelClient", "AgentReasoner"]
