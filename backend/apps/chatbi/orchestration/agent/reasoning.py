"""Function Calling ReAct 的单轮推理。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import orjson

from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.messages import (
    AgentMessage,
    ModelDecision,
    fold_tool_messages,
)
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tool_visibility import visible_tool_names
from apps.chatbi.orchestration.agent.working_state import (
    executable_sql,
    project_working_state,
)
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
        available_tools = self.available_tool_names(state, mode)
        invoke_messages = self._invoke_messages(state, mode, available_tools)
        tool_definitions = self._registry.definitions(allowed=available_tools)
        with self._tracer.span(
            "chat",
            llm_attributes(model=self._model_client.__class__.__name__),
        ) as llm_span:
            model_decision = self._model_client.invoke(
                invoke_messages, tool_definitions
            )
            tool_calls = [
                self._prepare_tool_call(state, call, available_tools)
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
        available_tools: list[str],
    ) -> list[AgentMessage]:
        system = state.require_system()
        working_state = AgentMessage.user(
            "<agent-working-state>"
            + orjson.dumps(project_working_state(state, mode, available_tools)).decode()
            + "</agent-working-state>\n"
            "该状态由服务端根据可信工具结果生成。请优先选择 recommended 动作；"
            "只有新动作能够补充缺失信息或修正上一错误时，才进行额外探索。"
        )
        if mode == "soft":
            return [
                system,
                AgentMessage.user(
                    "<system-reminder>预算接近上限。已有 SQL 时立即 execute_sql，"
                    "已有执行结果时立即 finish；若关键歧义未消可 clarify；"
                    "不要启动新的检索或 SQL 探索。</system-reminder>"
                ),
                working_state,
                *state.messages,
            ]
        return [system, working_state, *state.messages]

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

    def _prepare_tool_call(
        self,
        state: AgentRuntimeState,
        call: ToolCall,
        available_tools: list[str],
    ) -> ToolCall:
        """应用工具参数规则，并让执行动作使用服务端已有的可信 SQL。"""

        # 编译和执行阶段只有一个合法后继动作；供应商模型偶尔仍返回上一阶段工具，
        # 此时按可信进展纠正工具名，避免把一次可恢复偏差消耗成完整失败步骤。
        corrected_args: dict[str, Any] | None = None
        if call.name not in available_tools and available_tools == [
            "compile_semantic_sql"
        ]:
            corrected_args = {}
        elif call.name not in available_tools and available_tools == ["execute_sql"]:
            corrected_args = {}
        elif call.name not in available_tools and available_tools == ["finish"]:
            finish_tool = self._registry.get("finish")
            if (
                finish_tool is not None
                and "answer_markdown" in finish_tool.args_model.model_fields
            ):
                corrected_args = {"answer_markdown": "查询已执行完成。"}
        if corrected_args is not None:
            call = ToolCall(
                name=available_tools[0],
                args=corrected_args,
                call_id=call.call_id,
            )
        prepared = self._registry.prepare_call(call, state.context)
        sql = executable_sql(state.context.state)
        if prepared.name != "execute_sql" or sql is None:
            return prepared
        return ToolCall(
            name=prepared.name,
            args={"sql": sql},
            call_id=prepared.call_id,
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
