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
from apps.chatbi.orchestration.agent.tools.interaction import (
    prepare_semantic_clarification_args,
)
from apps.chatbi.orchestration.agent.working_state import (
    executable_sql,
    project_working_state,
)
from apps.tool import ToolCall, ToolDefinition, ToolRegistry
from apps.trace import (
    AgentTraceRecorder,
    TraceNodeSpec,
    TraceNodeType,
    llm_attributes,
)


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
        recorder: AgentTraceRecorder,
    ) -> None:
        self._config = config
        self._model_client = model_client
        self._registry = registry
        self._recorder = recorder

    def decide(
        self,
        state: AgentRuntimeState,
        mode: str,
        *,
        step_id: int | None = None,
        step_index: int | None = None,
    ) -> AgentDecision:
        """执行一轮 Reason，并把模型响应转换为结构化决策。"""

        if mode not in {"normal", "soft"}:
            raise ValueError(f"Unsupported reasoning mode: {mode}")
        run_id = state.require_run_id()
        with self._recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"reasoning_context:{step_index or 'unknown'}",
                node_type=TraceNodeType.PHASE,
                name="prepare_reasoning_context",
                display_name="准备推理上下文",
                metadata={"mode": mode, "step_id": step_id},
            ),
            input_data={
                "message_count": len(state.messages),
                "message_chars": sum(len(item.content) for item in state.messages),
                "mode": mode,
            },
        ) as context_node:
            fold_tool_messages(state.messages, self._config.context_fold_chars)
            available_tools = self.available_tool_names(state, mode)
            working_state = project_working_state(state, mode, available_tools)
            invoke_messages = self._invoke_messages(
                state,
                mode,
                available_tools,
                working_state,
            )
            tool_definitions = self._registry.definitions(allowed=available_tools)
            context_node.set_output(
                {
                    "message_count": len(invoke_messages),
                    "available_tool_count": len(tool_definitions),
                    "available_tools": available_tools,
                }
            )
            context_node.set_output_detail(
                {
                    "working_state": working_state,
                    "tool_definitions": [
                        item.model_dump(mode="json") for item in tool_definitions
                    ],
                }
            )
        llm_attributes_data = llm_attributes(
            model=self._model_client.__class__.__name__
        )
        if step_id is not None:
            llm_attributes_data["app.step.id"] = step_id
        with self._recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"agent_reasoning:{step_index or 'unknown'}",
                node_type=TraceNodeType.LLM,
                name="chat",
                display_name="Agent 推理模型",
                attributes=llm_attributes_data,
                metadata={"mode": mode, "step_id": step_id},
            ),
            input_data={
                "message_count": len(invoke_messages),
                "available_tool_count": len(tool_definitions),
                "mode": mode,
            },
            input_detail={
                "messages": [
                    item.model_dump(mode="json") for item in invoke_messages
                ],
                "tool_definitions": [
                    item.model_dump(mode="json") for item in tool_definitions
                ],
            },
        ) as llm_node:
            model_decision = self._model_client.invoke(
                invoke_messages, tool_definitions
            )
            usage = model_decision.usage
            llm_node.set_output(
                {
                    "tool_call_count": len(model_decision.tool_calls),
                    "direct_answer": not model_decision.tool_calls,
                }
            )
            llm_node.set_output_detail(
                {
                    "response": model_decision.message.model_dump(mode="json"),
                    "tool_calls": [
                        _tool_call_payload(call)
                        for call in model_decision.tool_calls
                    ],
                }
            )
            llm_node.set_token_usage(usage)
            for source, attribute in (
                ("input_tokens", "gen_ai.usage.input_tokens"),
                ("output_tokens", "gen_ai.usage.output_tokens"),
                ("total_tokens", "gen_ai.usage.total_tokens"),
            ):
                if usage.get(source) is not None:
                    llm_node.set_attribute(attribute, int(usage[source]))

        with self._recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"decision_projection:{step_index or 'unknown'}",
                node_type=TraceNodeType.PROJECTION,
                name="project_agent_decision",
                display_name="校验并整理模型决策",
                metadata={"mode": mode, "step_id": step_id},
            ),
            input_data={
                "original_tool_call_count": len(model_decision.tool_calls),
                "available_tools": available_tools,
            },
            input_detail={
                "original_tool_calls": [
                    _tool_call_payload(call) for call in model_decision.tool_calls
                ]
            },
        ) as projection_node:
            tool_calls = [
                self._prepare_tool_call(state, call, available_tools)
                for call in model_decision.tool_calls
            ]
            response = model_decision.message.model_copy(
                update={"tool_calls": tool_calls}
            )
            adjustments = _record_tool_call_preparations(
                state,
                model_decision.tool_calls,
                tool_calls,
            )
            projection_node.set_output(
                {
                    "tool_call_count": len(tool_calls),
                    "direct_answer": not tool_calls,
                    "adjustment_count": len(adjustments),
                }
            )
            projection_node.set_output_detail(
                {
                    "prepared_tool_calls": [
                        _tool_call_payload(call) for call in tool_calls
                    ],
                    "adjustments": adjustments,
                    "response": response.model_dump(mode="json"),
                }
            )

        state.budget.record_llm_turn(usage)
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
        working_state_payload: dict[str, Any],
    ) -> list[AgentMessage]:
        system = state.require_system()
        working_state = AgentMessage.user(
            "<agent-working-state>"
            + orjson.dumps(working_state_payload).decode()
            + "</agent-working-state>\n"
            "该状态由服务端根据可信工具结果生成。请优先选择 recommended 动作；"
            "只有新动作能够补充缺失信息或修正上一错误时，才进行额外探索。"
        )
        dynamic_messages = []
        if state.runtime_context is not None:
            dynamic_messages.append(state.runtime_context)
        if mode == "soft":
            dynamic_messages.extend(
                [
                    AgentMessage.user(
                        "<system-reminder>预算接近上限。已有 SQL 时立即 execute_sql，"
                        "已有执行结果时立即 finish；若关键歧义未消可 clarify；"
                        "不要启动新的检索或 SQL 探索。</system-reminder>"
                    ),
                    working_state,
                ]
            )
        else:
            dynamic_messages.append(working_state)
        return [system, *state.messages, *dynamic_messages]

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
        if call.name == "clarify":
            clarification = prepare_semantic_clarification_args(
                state.context.state
            )
            if clarification is not None:
                # 语义歧义的槽位和候选只能来自服务端决策，模型只负责选择动作。
                call = ToolCall(
                    name=call.name,
                    args=clarification.model_dump(mode="json"),
                    call_id=call.call_id,
                )
        prepared = self._registry.prepare_call(call, state.context)
        if prepared.name == "compile_semantic_sql":
            scope = state.context.state.get("semantic_scope")
            query_plan = scope.get("query_plan") if isinstance(scope, dict) else None
            if (
                isinstance(scope, dict)
                and scope.get("semantic_enforcement") == "STRICT"
                and isinstance(query_plan, dict)
                and query_plan.get("fingerprint")
            ):
                # 严格模式只把当前可信计划的指纹交给工具，模型参数不参与语义绑定。
                return ToolCall(
                    name=prepared.name,
                    args={"plan_fingerprint": query_plan["fingerprint"]},
                    call_id=prepared.call_id,
                )
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
) -> list[dict[str, Any]]:
    """记录模型参数被可信工具计划调整的事实，供运行审计与问题定位。"""

    adjustments = []
    for original, prepared in zip(original_calls, prepared_calls, strict=True):
        if original.name == prepared.name and original.args == prepared.args:
            continue
        adjustment = {
            "tool_call_id": original.call_id,
            "tool_name": original.name,
            "original_args": original.args,
            "prepared_args": prepared.args,
        }
        if original.name != prepared.name:
            adjustment.update(
                {
                    "original_tool_name": original.name,
                    "prepared_tool_name": prepared.name,
                }
            )
        adjustments.append(adjustment)
    if not adjustments:
        return []
    history = state.context.state.setdefault("tool_call_preparations", [])
    if not isinstance(history, list):
        raise TypeError("AGENT_TOOL_CALL_PREPARATIONS_INVALID")
    history.extend(adjustments)
    del history[:-20]
    return adjustments


def _tool_call_payload(call: ToolCall) -> dict[str, Any]:
    """把 ToolCall 转换为 Trace 可序列化结构。"""

    return {"name": call.name, "args": call.args, "call_id": call.call_id}


__all__ = ["AgentDecision", "AgentModelClient", "AgentReasoner"]
