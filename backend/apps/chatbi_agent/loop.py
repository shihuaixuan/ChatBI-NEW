"""AgentLoop：LLM 自主规划 + 受控工具循环。

LLM 拥有：选择工具、组织参数、决定顺序、决定何时澄清与结束。
LLM 没有：越出白名单、绕过守护、超出预算（BudgetGuard 硬上限）。
状态即消息历史：run.messages 持久化除 system 外的全部消息，恢复=反序列化继续。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import orjson
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    messages_from_dict,
)
from langchain_core.messages import message_to_dict

from apps.chat.models.chat_model import ChatRecord
from apps.chatbi_agent import crud
from apps.chatbi_agent.budget import BudgetGuard
from apps.chatbi_agent.events import sse_event
from apps.chatbi_agent.models import (
    AgentErrorClass,
    AgentRunStatus,
    ChatbiAgentClarification,
    ChatbiAgentRun,
)
from apps.chatbi_agent.prompts import build_system_prompt
from apps.chatbi_agent.schemas import AgentConfig, AgentEventPayload
from apps.chatbi_agent.tools.base import AgentToolContext, ToolOutput
from apps.chatbi_agent.tools.core import build_default_tools
from apps.chatbi_agent.tools.interaction import (
    ClarifyTool,
    GetSqlExamplesTool,
    SearchTerminologyTool,
)
from apps.chatbi_agent.tools.registry import ToolRegistry

FOLDED_PLACEHOLDER = "（此前的工具结果已折叠归档，如需请重新调用工具）"


class DefaultAgentModelClient:
    """默认模型客户端：LLMFactory 默认配置 + bind_tools。可注入替身测试。"""

    def __init__(self) -> None:
        self._llm = None

    def invoke(self, messages: list, tool_specs: list[dict]):
        return self._get_llm().bind_tools(tool_specs).invoke(messages)

    def _get_llm(self):
        if self._llm is None:
            from apps.ai_model.model_factory import LLMFactory, get_default_config

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                config = asyncio.run(get_default_config())
            else:
                raise RuntimeError("agent model cannot be loaded inside a running event loop")
            self._llm = LLMFactory.create_llm(config).llm
        return self._llm


class AgentLoop:
    def __init__(
        self,
        session,
        current_user,
        config: AgentConfig | None = None,
        model_client=None,
        registry: ToolRegistry | None = None,
    ):
        self.session = session
        self.current_user = current_user
        self.config = config or AgentConfig()
        self.model_client = model_client or DefaultAgentModelClient()
        self.registry = registry or self._build_registry()

    def _build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        for tool in build_default_tools():
            registry.register(tool)
        registry.register(ClarifyTool())
        registry.register(SearchTerminologyTool())
        registry.register(GetSqlExamplesTool())
        return registry

    # ---- 入口 ----

    def run(self, run: ChatbiAgentRun, record: ChatRecord) -> Iterator[str]:
        budget = self._new_budget()
        ctx = self._new_ctx(run, record)
        messages = [HumanMessage(content=record.question or "")]
        system = self._build_system(run, record)

        crud.update_run(self.session, run, status=AgentRunStatus.RUNNING.value)
        crud.finish_record(self.session, record, AgentRunStatus.RUNNING.value)
        self.session.commit()
        yield self._emit(run, "record-created", {"record_id": record.id, "id": record.id, "run_id": run.id})
        yield self._emit(run, "run-started", {"record_id": record.id, "run_id": run.id})

        try:
            yield from self._loop(run, record, ctx, system, messages, budget)
        except Exception as exc:  # 任意未预期异常收敛为失败事件，避免 SSE 静默中断。
            message = str(exc) or exc.__class__.__name__
            yield from self._fail(run, record, messages, budget, message, AgentErrorClass.UNEXPECTED.value)

    def resume(
        self,
        run: ChatbiAgentRun,
        record: ChatRecord,
        clarification: ChatbiAgentClarification,
        answer_text: str,
    ) -> Iterator[str]:
        """澄清回答后恢复：答案以 ToolMessage 回填，预算从快照恢复，继续循环。"""

        budget = self._new_budget()
        budget.restore(run.budget_snapshot)
        ctx = self._new_ctx(run, record)
        messages = messages_from_dict(run.messages)
        messages.append(ToolMessage(content=answer_text, tool_call_id=clarification.tool_call_id or ""))
        system = self._build_system(run, record)

        crud.update_run(self.session, run, status=AgentRunStatus.RUNNING.value, messages=_serialize_messages(messages))
        crud.finish_record(self.session, record, AgentRunStatus.RUNNING.value)
        self.session.commit()
        yield self._emit(run, "clarification-accepted", {"record_id": record.id, "run_id": run.id})

        try:
            yield from self._loop(run, record, ctx, system, messages, budget)
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            yield from self._fail(run, record, messages, budget, message, AgentErrorClass.UNEXPECTED.value)

    # ---- 组装 ----

    def _new_budget(self) -> BudgetGuard:
        return BudgetGuard(
            max_steps=self.config.max_steps,
            token_budget=self.config.token_budget,
            repeat_fuse_threshold=self.config.repeat_fuse_threshold,
            max_sql_retries=self.config.max_sql_retries,
            timeout_seconds=self.config.timeout_seconds,
            max_clarifications=self.config.max_clarifications,
        )

    def _new_ctx(self, run: ChatbiAgentRun, record: ChatRecord) -> AgentToolContext:
        return AgentToolContext(
            session=self.session,
            oid=run.oid,
            user_id=self.current_user.id,
            datasource_id=record.datasource,
            config=self.config,
            state={"question": record.question or ""},
        )

    def _build_system(self, run: ChatbiAgentRun, record: ChatRecord) -> SystemMessage:
        history = crud.recent_qa_summaries(self.session, run.chat_id, record.id, limit=self.config.history_rounds)
        history_summary = None
        if history:
            history_summary = "\n".join(
                f"- 问：{item['question']}\n  SQL：{item['sql'] or '（无）'}\n  答（摘要）：{item['answer_brief']}"
                for item in history
            )
        pending = None
        found = crud.find_chat_pending_clarification(self.session, run.chat_id, record.id)
        if found:
            _, pending_clarification = found
            pending = {
                "question": pending_clarification.question,
                "options": pending_clarification.options or [],
            }
        return SystemMessage(
            content=build_system_prompt(
                datasource_id=record.datasource,
                oid=run.oid,
                max_clarifications=self.config.max_clarifications,
                history_summary=history_summary,
                pending_clarification=pending,
            )
        )

    # ---- 主循环 ----

    def _loop(self, run, record, ctx, system, messages, budget) -> Iterator[str]:
        while True:
            verdict = budget.check_before_step()
            if not verdict.allowed:
                yield from self._fail(run, record, messages, budget, verdict.reason, verdict.error_class)
                return

            _fold_messages(messages, self.config.context_fold_chars)
            step_index = budget.steps + 1
            step = crud.start_step(self.session, run, step_index, None, {})
            self.session.commit()
            yield self._emit(run, "step-started", {"record_id": record.id, "step_index": step_index}, step.id)

            response: AIMessage = self.model_client.invoke([system, *messages], self.registry.tool_specs())
            usage = getattr(response, "usage_metadata", None) or {}
            budget.record_llm_turn(usage)
            messages.append(response)

            text = _content_text(response)
            if text:
                yield self._emit(run, "thinking", {"record_id": record.id, "content": text}, step.id)

            tool_calls = list(getattr(response, "tool_calls", None) or [])
            if not tool_calls:
                # 宽松 finish：模型直接给出文本回答。
                crud.finish_step(self.session, step, {"mode": "direct_answer"}, usage)
                yield from self._finish(
                    run, record, messages, budget,
                    answer=text or "（模型未给出回答）",
                    chart={},
                    sql=(ctx.state.get("last_execution") or {}).get("sql"),
                    step_id=step.id,
                )
                return

            for tool_call in tool_calls:
                tool_name = tool_call.get("name") or ""
                raw_args = tool_call.get("args") or {}
                call_id = tool_call.get("id") or ""
                step.tool_name = tool_name
                step.args_summary = _args_summary(raw_args)
                self.session.add(step)
                yield self._emit(run, "tool-called", {"record_id": record.id, "tool_name": tool_name}, step.id)

                fuse = budget.check_tool_call(tool_name, raw_args)
                if not fuse.allowed:
                    crud.fail_step(self.session, step, fuse.reason)
                    yield from self._fail(run, record, messages, budget, fuse.reason, fuse.error_class)
                    return

                output = self.registry.execute(tool_name, ctx, raw_args)

                if tool_name == "clarify" and output.success:
                    clarify_verdict = budget.record_clarification()
                    if not clarify_verdict.allowed:
                        # 超出澄清预算不挂起：告知模型基于现有信息收敛。
                        messages.append(
                            ToolMessage(
                                content="澄清次数已达上限，请基于现有信息继续，或如实说明无法完成。",
                                tool_call_id=call_id,
                            )
                        )
                        crud.finish_step(self.session, step, {"tool": "clarify", "rejected": "budget"}, usage)
                        continue
                    crud.finish_step(self.session, step, {"tool": "clarify"}, usage)
                    yield from self._suspend_for_clarification(run, record, messages, budget, output, call_id, step.id)
                    return

                messages.append(ToolMessage(content=output.summary, tool_call_id=call_id))

                if tool_name == "finish" and output.success:
                    crud.finish_step(self.session, step, {"tool": "finish"}, usage)
                    payload = output.payload
                    if payload.get("chart"):
                        yield self._emit(run, "chart-generated", {"record_id": record.id, "chart": payload["chart"]}, step.id)
                    yield from self._finish(
                        run, record, messages, budget,
                        answer=payload.get("answer") or "",
                        chart=payload.get("chart") or {},
                        sql=payload.get("sql"),
                        step_id=step.id,
                        full_data=ctx.state.get("full_data"),
                        execution=ctx.state.get("last_execution"),
                    )
                    return

                result_summary = _result_summary(tool_name, output)
                if output.success:
                    crud.finish_step(self.session, step, result_summary, usage)
                else:
                    crud.fail_step(self.session, step, output.summary[:500])
                yield self._emit(run, "tool-result", {"record_id": record.id, "tool_name": tool_name, **result_summary}, step.id)
                for event_type, payload in _semantic_events(tool_name, output, record.id):
                    yield self._emit(run, event_type, payload, step.id)

                if tool_name == "execute_sql" and not output.success:
                    retry = budget.record_sql_failure()
                    if not retry.allowed:
                        yield from self._fail(run, record, messages, budget, retry.reason, retry.error_class)
                        return

            crud.update_run(
                self.session, run,
                messages=_serialize_messages(messages),
                budget_snapshot=budget.snapshot(),
            )
            self.session.commit()

    # ---- 终态与挂起 ----

    def _suspend_for_clarification(self, run, record, messages, budget, output: ToolOutput, call_id: str, step_id) -> Iterator[str]:
        clarification = crud.create_clarification(
            self.session,
            run,
            question=output.payload["question"],
            options=output.payload.get("options") or [],
            tool_call_id=call_id,
            user_id=self.current_user.id,
        )
        crud.finish_record(self.session, record, AgentRunStatus.WAITING_USER.value)
        crud.update_run(
            self.session, run,
            status=AgentRunStatus.WAITING_USER.value,
            messages=_serialize_messages(messages),
            budget_snapshot=budget.snapshot(),
        )
        self.session.commit()
        yield self._emit(
            run,
            "clarification",
            {
                "record_id": record.id,
                "clarification_id": clarification.id,
                "question": clarification.question,
                "options": clarification.options or [],
            },
            step_id,
        )

    def _finish(self, run, record, messages, budget, *, answer, chart, sql, step_id=None, full_data=None, execution=None) -> Iterator[str]:
        record.sql_answer = answer
        record.chart_answer = answer
        record.sql = sql
        record.chart = orjson.dumps(chart or {}).decode()
        if full_data is not None and execution:
            record.data = orjson.dumps({"fields": execution.get("fields") or [], "data": full_data}).decode()
        crud.finish_record(self.session, record, AgentRunStatus.FINISHED.value)
        crud.update_run(
            self.session, run,
            status=AgentRunStatus.FINISHED.value,
            messages=_serialize_messages(messages),
            budget_snapshot=budget.snapshot(),
        )
        self.session.add(record)
        self.session.commit()
        yield self._emit(run, "answer", {"record_id": record.id, "content": answer}, step_id)
        yield self._emit(run, "run-finished", {"record_id": record.id, "content": answer}, step_id)
        yield self._emit(run, "finish", {"record_id": record.id, "content": answer}, step_id)

    def _fail(self, run, record, messages, budget, message, error_class) -> Iterator[str]:
        crud.finish_record(self.session, record, AgentRunStatus.FAILED.value, message)
        crud.update_run(
            self.session, run,
            status=AgentRunStatus.FAILED.value,
            messages=_serialize_messages(messages),
            budget_snapshot=budget.snapshot(),
            error_class=error_class,
            error=message,
        )
        self.session.commit()
        yield self._emit(run, "run-failed", {"record_id": record.id, "content": message, "error_class": error_class})
        yield self._emit(run, "error", {"record_id": record.id, "content": message, "error_class": error_class})

    def _emit(self, run: ChatbiAgentRun, event_type: str, payload: dict, step_id: int | None = None) -> str:
        event = crud.append_trace(self.session, run.id, event_type, payload, step_id=step_id)
        self.session.commit()
        return sse_event(
            AgentEventPayload(
                type=event_type,
                content=payload,
                record_id=payload.get("record_id"),
                run_id=run.id,
                sequence=event.sequence,
            )
        )


# ---- 序列化与摘要 ----


def _serialize_messages(messages: list) -> list[dict]:
    return [message_to_dict(message) for message in messages]


def _fold_messages(messages: list, max_chars: int, keep_recent: int = 6) -> None:
    """消息历史超过阈值时，把较早的工具结果折叠为占位符（就地修改，持久化同步收缩）。"""

    if max_chars <= 0:
        return
    total = sum(len(str(getattr(message, "content", ""))) for message in messages)
    if total <= max_chars:
        return
    for message in messages[:-keep_recent]:
        if isinstance(message, ToolMessage) and message.content != FOLDED_PLACEHOLDER:
            message.content = FOLDED_PLACEHOLDER


def _content_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
    return ""


def _args_summary(args: dict) -> dict:
    encoded = orjson.dumps(args).decode()
    if len(encoded) > 2000:
        return {"_truncated": encoded[:2000]}
    return args


def _result_summary(tool_name: str, output: ToolOutput) -> dict:
    if not output.success:
        return {"success": False, "error_code": output.error_code}
    payload = output.payload
    if tool_name == "execute_sql":
        return {"success": True, "row_count": payload.get("row_count"), "fields": payload.get("fields")}
    if tool_name in {"compile_semantic_sql", "validate_sql"}:
        return {"success": True, "sql": payload.get("sql")}
    if tool_name == "search_semantic_assets":
        return {
            "success": True,
            "status": payload.get("status"),
            "metrics": payload.get("metrics"),
            "dimensions": payload.get("dimensions"),
            "tables": payload.get("tables"),
        }
    if tool_name == "get_dataset_schema":
        return {"success": True, "table_count": payload.get("table_count")}
    if tool_name in {"search_terminology", "get_sql_examples"}:
        return {"success": True, "count": payload.get("count")}
    return {"success": True}


def _semantic_events(tool_name: str, output: ToolOutput, record_id: int) -> list[tuple[str, dict]]:
    if not output.success:
        return []
    payload = output.payload
    if tool_name == "compile_semantic_sql":
        return [("sql-generated", {"record_id": record_id, "sql": payload.get("sql")})]
    if tool_name == "validate_sql":
        return [("sql-validated", {"record_id": record_id, "sql": payload.get("sql")})]
    if tool_name == "execute_sql":
        return [
            (
                "sql-executed",
                {"record_id": record_id, "row_count": payload.get("row_count"), "fields": payload.get("fields")},
            )
        ]
    return []
