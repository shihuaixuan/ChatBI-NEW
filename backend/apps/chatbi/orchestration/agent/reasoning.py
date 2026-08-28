"""Function Calling ReAct 的单轮推理。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, Thread
from typing import Any, Protocol

import orjson
from pydantic import ValidationError

from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.models.dto.research_agent import ResearchTurnDecision
from apps.chatbi.orchestration.agent.cancellation import (
    AgentCancellationRequested,
    CancellationStage,
)
from apps.chatbi.orchestration.agent.cancellation_guard import CancellationGuard
from apps.chatbi.orchestration.agent.messages import (
    AgentMessage,
    ModelDecision,
    close_unfinished_tool_calls,
    fold_tool_messages,
)
from apps.chatbi.orchestration.agent.reasoning_profile import (
    ReasoningProfile,
    get_reasoning_profile,
)
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tools.interaction import (
    prepare_semantic_clarification_args,
)
from apps.chatbi.orchestration.agent.working_state import executable_sql
from apps.tool import ToolCall, ToolDefinition, ToolRegistry
from apps.trace import (
    AgentTraceRecorder,
    TraceNodeSpec,
    TraceNodeType,
    llm_attributes,
)

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class ResearchAgentDecision:
    """Research Agent 解析后的单轮决策。"""

    decision: ResearchTurnDecision | None
    response: AgentMessage
    usage: dict[str, Any]
    repaired: bool = False

    @property
    def is_direct_answer(self) -> bool:
        """没有工具调用时，正文直接作为最终回答。"""

        return self.decision is None


class ResearchDecisionParseError(ValueError):
    """ResearchTurnDecision 解析失败，携带可审计的稳定错误信息。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        raw_response_excerpt: str = "",
        model_turns: int = 1,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.raw_response_excerpt = raw_response_excerpt[:2_000]
        self.model_turns = model_turns


@dataclass
class _ModelInvocationResult:
    decision: ModelDecision | None = None
    error: Exception | None = None


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
        profile: ReasoningProfile | None = None,
        step_id: int | None = None,
        step_index: int | None = None,
    ) -> AgentDecision:
        """执行一轮 Reason，并把模型响应转换为结构化决策。

        ``mode`` 仍用于 Trace 标注；行为差异全部由 Reasoning Profile 决定，
        宿主可以用 ``profile`` 注入携带运行时闭包的模式实例（Research）。
        """

        resolved_profile = (
            profile if profile is not None else get_reasoning_profile(mode)
        )
        if resolved_profile.prompt_version is not None:
            # 将当前提示词版本写入可持久化运行上下文，便于恢复和回放时审计。
            state.context.state["research_prompt_version"] = (
                resolved_profile.prompt_version
            )
        run_id = state.require_run_id()
        with self._recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"reasoning_context:{step_index or 'unknown'}",
                node_type=TraceNodeType.PHASE,
                name="prepare_reasoning_context",
                display_name="准备推理上下文",
                metadata={
                    "mode": resolved_profile.name,
                    "step_id": step_id,
                    "prompt_version": resolved_profile.prompt_version,
                },
            ),
            input_data={
                "message_count": len(state.messages),
                "message_chars": sum(len(item.content) for item in state.messages),
                "mode": resolved_profile.name,
                "prompt_version": resolved_profile.prompt_version,
            },
        ) as context_node:
            fold_tool_messages(state.messages, self._config.context_fold_chars)
            available_tools = resolved_profile.visible_tool_names(
                state,
                self._registry.names(),
            )
            working_state = resolved_profile.project_working_state(
                state,
                available_tools,
            )
            invoke_messages = self._invoke_messages(
                state,
                resolved_profile,
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
                metadata={
                    "mode": resolved_profile.name,
                    "step_id": step_id,
                    "prompt_version": resolved_profile.prompt_version,
                },
            ),
            input_data={
                "message_count": len(invoke_messages),
                "available_tool_count": len(tool_definitions),
                "mode": resolved_profile.name,
                "prompt_version": resolved_profile.prompt_version,
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
            model_decision = _invoke_model_with_cancellation(
                state,
                self._model_client,
                invoke_messages,
                tool_definitions,
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
            metadata={
                "mode": resolved_profile.name,
                "step_id": step_id,
                "prompt_version": resolved_profile.prompt_version,
            },
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
            if resolved_profile.fixed_tool_allowlist is not None:
                # 固定白名单模式（Research）不做陈旧工具名纠正：越界选择必须
                # 原样交给宿主批次校验拒绝，不能被静默改写成别的工具。
                tool_calls = list(model_decision.tool_calls)
            else:
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

    def decide_research(
        self,
        state: AgentRuntimeState,
        *,
        profile: ReasoningProfile | None = None,
        step_id: int | None = None,
        step_index: int | None = None,
    ) -> ResearchAgentDecision:
        """调用一次 Research Agent，并允许一次结构修正重试。"""

        resolved_profile = profile or get_reasoning_profile("research_react")
        available_tools = resolved_profile.visible_tool_names(
            state,
            self._registry.names(),
        )
        first = self.decide(
            state,
            resolved_profile.name,
            profile=resolved_profile,
            step_id=step_id,
            step_index=step_index,
        )
        if first.is_direct_answer:
            return ResearchAgentDecision(
                decision=None,
                response=first.response,
                usage=first.usage,
            )
        try:
            parsed = parse_research_turn_decision(
                first,
                available_tools=available_tools,
            )
        except ResearchDecisionParseError as first_error:
            _record_research_decision_error(state, first_error, repaired=False)
            # 上一次模型消息即使未通过 ResearchTurnDecision 校验，也可能已经
            # 携带 tool_calls。再次请求模型前必须先补齐 tool 消息，否则 OpenAI
            # 兼容接口会拒绝“不完整的 assistant tool_calls 消息历史”。
            close_unfinished_tool_calls(
                state.messages,
                content="skipped: ResearchTurnDecision 校验失败，工具未执行",
            )
            state.messages.append(
                AgentMessage.user(
                    "上一次 Research Agent 输出未通过 ResearchTurnDecision 校验。"
                    "请只重新提交符合当前工具 JSON Schema 的结构化动作；不要输出解释文本。"
                    f"错误代码：{first_error.code}。"
                )
            )
            repaired = self.decide(
                state,
                resolved_profile.name,
                profile=resolved_profile,
                step_id=step_id,
                step_index=step_index,
            )
            try:
                parsed = parse_research_turn_decision(
                    repaired,
                    available_tools=available_tools,
                )
            except ResearchDecisionParseError as second_error:
                _record_research_decision_error(state, second_error, repaired=True)
                # 修正请求仍可能返回不合规 tool_calls；交回外层循环前保持消息
                # 历史完整，下一轮才能继续请求模型并保留可审计的失败事实。
                close_unfinished_tool_calls(
                    state.messages,
                    content="skipped: ResearchTurnDecision 修正仍未通过校验",
                )
                raise ResearchDecisionParseError(
                    "RESEARCH_AGENT_DECISION_REPAIR_FAILED",
                    second_error.message,
                    raw_response_excerpt=second_error.raw_response_excerpt,
                    model_turns=first_error.model_turns + second_error.model_turns,
                ) from second_error
            self._record_research_turn_trace(
                state,
                parsed,
                step_id=step_id,
                step_index=step_index,
                repaired=True,
            )
            return ResearchAgentDecision(
                decision=parsed,
                response=repaired.response,
                usage=repaired.usage,
                repaired=True,
            )
        self._record_research_turn_trace(
            state,
            parsed,
            step_id=step_id,
            step_index=step_index,
            repaired=False,
        )
        return ResearchAgentDecision(
            decision=parsed,
            response=first.response,
            usage=first.usage,
        )

    def _record_research_turn_trace(
        self,
        state: AgentRuntimeState,
        decision: ResearchTurnDecision,
        *,
        step_id: int | None,
        step_index: int | None,
        repaired: bool,
    ) -> None:
        """记录通过契约校验的 ResearchTurnDecision。"""

        run_id = state.require_run_id()
        prompt_version = state.context.state.get("research_prompt_version")
        with self._recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"research_turn_decision:{step_index or 'unknown'}",
                node_type=TraceNodeType.PROJECTION,
                name="research_turn_decision",
                display_name="Research 轮次决策",
                metadata={
                    "step_id": step_id,
                    "prompt_version": prompt_version,
                    "repaired": repaired,
                },
            ),
            input_data={
                "step_id": step_id,
                "step_index": step_index,
                "prompt_version": prompt_version,
                "repaired": repaired,
            },
        ) as decision_node:
            decision_node.set_output(
                {
                    "action_count": len(decision.actions),
                    "repaired": repaired,
                }
            )
            decision_node.set_output_detail(
                {"research_turn_decision": decision.model_dump(mode="json")}
            )

    def _invoke_messages(
        self,
        state: AgentRuntimeState,
        profile: ReasoningProfile,
        available_tools: list[str],
        working_state_payload: dict[str, Any],
    ) -> list[AgentMessage]:
        system = (
            AgentMessage.system(profile.system_prompt)
            if profile.system_prompt is not None
            else state.require_system()
        )
        working_state = AgentMessage.user(
            "<agent-working-state>"
            + orjson.dumps(working_state_payload).decode()
            + "</agent-working-state>\n"
            + profile.working_state_note
        )
        dynamic_messages = []
        if state.runtime_context is not None:
            dynamic_messages.append(state.runtime_context)
        if profile.soft_reminder is not None:
            dynamic_messages.append(AgentMessage.user(profile.soft_reminder))
        dynamic_messages.append(working_state)
        return [system, *state.messages, *dynamic_messages]

    def available_tool_names(
        self,
        state: AgentRuntimeState,
        mode: str,
    ) -> list[str]:
        profile = get_reasoning_profile(mode)
        return profile.visible_tool_names(state, self._registry.names())

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
            if finish_tool is not None and not finish_tool.args_model.model_fields:
                # 新版 finish 不接收模型生成内容，只需使用服务端查询结果收口。
                corrected_args = {}
            elif (
                finish_tool is not None
                and "answer_markdown" in finish_tool.args_model.model_fields
            ):
                # 保留旧宿主工具的参数约定，避免测试工具或扩展工具被错误改写。
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


def _invoke_model_with_cancellation(
    state: AgentRuntimeState,
    model_client: AgentModelClient,
    messages: list[AgentMessage],
    tool_definitions: list[ToolDefinition],
) -> ModelDecision:
    """在模型请求期间轮询取消，并在取消后丢弃模型结果。"""

    guard = CancellationGuard(state.cancellation)
    guard.require_not_requested(CancellationStage.BEFORE_LLM)
    result_queue: Queue[_ModelInvocationResult] = Queue(maxsize=1)
    detached = Event()

    def invoke() -> None:
        try:
            decision = model_client.invoke(messages, tool_definitions)
        except Exception as exc:
            if detached.is_set():
                logger.warning("LLM 请求在取消后返回异常，结果已丢弃", exc_info=True)
            result_queue.put(_ModelInvocationResult(error=exc))
            return
        result_queue.put(_ModelInvocationResult(decision=decision))

    Thread(
        target=invoke,
        name=f"agent-llm-{state.require_run_id()}",
        daemon=True,
    ).start()

    while True:
        try:
            result = result_queue.get(timeout=0.05)
        except Empty:
            if guard.check(CancellationStage.DURING_LLM):
                detached.set()
                raise AgentCancellationRequested(CancellationStage.DURING_LLM)
            continue
        if result.error is not None:
            raise result.error
        if result.decision is None:
            raise RuntimeError("AGENT_MODEL_RESULT_MISSING")
        guard.require_not_requested(CancellationStage.AFTER_LLM)
        return result.decision


def parse_research_turn_decision(
    model_decision: AgentDecision,
    *,
    available_tools: Sequence[str],
) -> ResearchTurnDecision:
    """将模型的 JSON 或工具调用解析为 ResearchTurnDecision。"""

    allowed_tools = set(available_tools)
    if model_decision.tool_calls:
        # 工具调用是唯一结构化协议；正文只作为说明，不再承载隐藏状态 sidecar。
        actions: list[dict[str, Any]] = []
        for call in model_decision.tool_calls:
            if not call.call_id:
                raise ResearchDecisionParseError(
                    "RESEARCH_AGENT_TOOL_CALL_ID_MISSING",
                    "工具调用缺少 call_id。",
                )
            if call.name not in allowed_tools:
                raise ResearchDecisionParseError(
                    "RESEARCH_AGENT_DECISION_TOOL_NOT_VISIBLE",
                    f"工具 {call.name} 不在本轮可见工具集合中。",
                )
            if not isinstance(call.args, dict):
                raise ResearchDecisionParseError(
                    "RESEARCH_AGENT_DECISION_ARGUMENTS_INVALID",
                    f"工具 {call.name} 的 arguments 不是对象。",
                )
            arguments = dict(call.args)
            # DeepSeek 等兼容接口会把 query_semantic_data 参数再包一层，且
            # 已观察到 query 和工具名两种外包键；仅对该工具解包，避免放宽
            # 其他工具的参数校验。
            if call.name == "query_semantic_data":
                wrapper_keys = set(arguments)
                if wrapper_keys in ({"query"}, {call.name}):
                    wrapper_key = next(iter(wrapper_keys))
                    if isinstance(arguments[wrapper_key], dict):
                        arguments = dict(arguments[wrapper_key])
            purpose = arguments.pop("purpose", f"执行 {call.name}")
            expected_result = arguments.pop("expected_result", None)
            actions.append(
                {
                    "action_type": call.name,
                    "purpose": purpose,
                    "expected_result": expected_result,
                    "arguments": arguments,
                }
            )
        payload = {"actions": actions}
    else:
        content = model_decision.response.content.strip()
        if not content:
            raise ResearchDecisionParseError(
                "RESEARCH_AGENT_DECISION_ACTION_REQUIRED",
                "模型没有返回动作或结构化决策。",
            )
        try:
            payload = orjson.loads(content)
        except orjson.JSONDecodeError as exc:
            raise ResearchDecisionParseError(
                "RESEARCH_AGENT_DECISION_JSON_INVALID",
                "模型正文不是合法 JSON。",
                raw_response_excerpt=content,
            ) from exc
        if not isinstance(payload, dict):
            raise ResearchDecisionParseError(
                "RESEARCH_AGENT_DECISION_OBJECT_REQUIRED",
                "ResearchTurnDecision 必须是 JSON 对象。",
                raw_response_excerpt=content,
            )

    try:
        decision = ResearchTurnDecision.model_validate(payload)
    except ValidationError as exc:
        raise ResearchDecisionParseError(
            "RESEARCH_AGENT_DECISION_SCHEMA_INVALID",
            str(exc).splitlines()[0],
            raw_response_excerpt=model_decision.response.content,
        ) from exc
    _validate_research_decision_tools(decision, allowed_tools)
    return decision


def _validate_research_decision_tools(
    decision: ResearchTurnDecision,
    allowed_tools: set[str],
) -> None:
    """防止 JSON 正文绕过当前可见工具集合。"""

    hidden = sorted(
        {
            action.action_type.value
            for action in decision.actions
            if action.action_type.value not in allowed_tools
        }
    )
    if hidden:
        raise ResearchDecisionParseError(
            "RESEARCH_AGENT_DECISION_TOOL_NOT_VISIBLE",
            f"工具不在本轮可见工具集合中：{', '.join(hidden)}。",
        )


def _record_research_decision_error(
    state: AgentRuntimeState,
    error: ResearchDecisionParseError,
    *,
    repaired: bool,
) -> None:
    """保存解析失败摘要，不保存完整模型输出到运行状态。"""

    state.context.state["research_decision_parse_error"] = {
        "code": error.code,
        "message": error.message,
        "raw_response_excerpt": error.raw_response_excerpt,
        "repair_attempted": repaired,
    }


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


__all__ = [
    "AgentDecision",
    "AgentModelClient",
    "AgentReasoner",
    "ResearchAgentDecision",
    "ResearchDecisionParseError",
    "parse_research_turn_decision",
]
