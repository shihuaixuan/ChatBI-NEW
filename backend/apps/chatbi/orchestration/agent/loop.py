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
    message_to_dict,
    messages_from_dict,
)

from apps.chatbi.composition import (
    build_chat_record_service,
    build_physical_schema_service,
    build_query_service,
    build_question_understanding_service,
    build_result_artifact_service,
    build_semantic_query_service,
    build_semantic_retrieval_service,
)
from apps.chatbi.errors import QuestionUnderstandingError
from apps.chatbi.models import (
    AgentClarificationResumeKind,
    AgentErrorClass,
    AgentRunStatus,
    ChatbiAgentClarification,
    ChatbiAgentRun,
    ChatRecord,
    ChatRecordExecutionType,
    ChatRecordResultProjection,
    ChatRecordStatus,
)
from apps.chatbi.models.dto.agent import AgentConfig, AgentEventPayload
from apps.chatbi.orchestration.agent.budget import BudgetGuard
from apps.chatbi.orchestration.agent.events import sse_event
from apps.chatbi.orchestration.agent.prompts import build_system_prompt
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext, ToolOutput
from apps.chatbi.orchestration.agent.tools.core import build_default_tools
from apps.chatbi.orchestration.agent.tools.interaction import (
    ClarifyTool,
    GetSqlExamplesTool,
    SearchTerminologyTool,
)
from apps.chatbi.orchestration.agent.tools.registry import ToolRegistry
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.execution import (
    GuardedQueryService,
    ResultArtifactService,
)
from apps.chatbi.services.planning import (
    PhysicalSchemaService,
    SemanticCompilationService,
    SemanticRetrievalService,
)
from apps.chatbi.services.understanding import (
    QuestionUnderstandingService,
    apply_question_understanding_clarification,
)
from apps.semantic.composition import build_semantic_term_query_service
from apps.semantic.services.term_query_service import SemanticTermQueryService

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
        understanding_service: QuestionUnderstandingService | None = None,
        term_query_service: SemanticTermQueryService | None = None,
        query_service: GuardedQueryService | None = None,
        semantic_query_service: SemanticCompilationService | None = None,
        semantic_retrieval_service: SemanticRetrievalService | None = None,
        physical_schema_service: PhysicalSchemaService | None = None,
        result_artifact_service: ResultArtifactService | None = None,
    ):
        self.session = session
        self.current_user = current_user
        self.config = config or AgentConfig()
        self.record_service = build_chat_record_service(session)
        self.model_client = model_client or DefaultAgentModelClient()
        self.registry = registry or self._build_registry()
        self.understanding_service = (
            understanding_service or build_question_understanding_service()
        )
        self.term_query_service = term_query_service or build_semantic_term_query_service(
            session
        )
        self.query_service = query_service or build_query_service(
            session,
            default_limit=self.config.default_limit,
            sample_rows=self.config.sample_rows,
        )
        self.semantic_query_service = (
            semantic_query_service or build_semantic_query_service(session)
        )
        self.semantic_retrieval_service = (
            semantic_retrieval_service
            or build_semantic_retrieval_service(session)
        )
        self.physical_schema_service = (
            physical_schema_service or build_physical_schema_service(session)
        )
        self.result_artifact_service = (
            result_artifact_service
            or build_result_artifact_service(session)
        )

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

        agent_run_repository.update_run(self.session, run, status=AgentRunStatus.RUNNING.value)
        self.record_service.transition(
            record,
            ChatRecordStatus.RUNNING,
            execution_type=ChatRecordExecutionType.AGENT,
        )
        self.session.commit()
        yield self._emit(run, "record-created", {"record_id": record.id, "id": record.id, "run_id": run.id})
        yield self._emit(run, "run-started", {"record_id": record.id, "run_id": run.id})

        try:
            conversation_context = self._load_conversation_context(run, record)
            understanding_context = {
                "last_rewritten_question": conversation_context.get("last_rewritten_question"),
            }
            outcome = self.understanding_service.understand(
                question=record.question or "",
                datasource_id=record.datasource,
                conversation_context=understanding_context,
            )
            budget.record_llm_usage(outcome.usage_metadata)
            understanding = outcome.output.model_dump(mode="json")
            ctx.state.update(
                {
                    "original_question": record.question or "",
                    "question": outcome.output.rewritten_question,
                    "question_understanding": understanding,
                }
            )
            messages = [HumanMessage(content=outcome.output.rewritten_question)]
            system = self._build_system(
                run,
                record,
                conversation_context=conversation_context,
                question_understanding=understanding,
            )
            agent_run_repository.update_run(
                self.session,
                run,
                messages=_serialize_messages(messages),
                budget_snapshot=budget.snapshot(),
                derived_state=_persistable_state(ctx.state),
            )
            self.session.commit()
            yield self._emit(
                run,
                "question-understood",
                {
                    "record_id": record.id,
                    "message_type": outcome.output.message_type,
                    "rewritten_question": outcome.output.rewritten_question,
                    "intent_type": outcome.output.intent.intent_type,
                    "confidence": outcome.output.intent.confidence,
                    "validation": outcome.output.validation.model_dump(mode="json"),
                },
            )
            preflight = self._understanding_preflight_clarification(understanding)
            if preflight is not None:
                yield from self._suspend_for_preflight_clarification(
                    run,
                    record,
                    ctx,
                    messages,
                    budget,
                    preflight,
                )
                return
            yield from self._loop(run, record, ctx, system, messages, budget)
        except QuestionUnderstandingError as exc:
            yield from self._fail(
                run,
                record,
                messages,
                budget,
                str(exc),
                AgentErrorClass.UNDERSTANDING.value,
            )
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
        """从澄清记录声明的恢复边界继续，不重跑已经完成的问题理解。"""

        budget = self._new_budget()
        budget.restore(run.budget_snapshot)
        ctx = self._new_ctx(run, record)
        # 回填挂起前的派生状态（语义资产集合、白名单表等），避免恢复后被迫重新检索。
        ctx.state.update(run.derived_state or {})
        messages = messages_from_dict(run.messages)

        try:
            try:
                resume_kind = AgentClarificationResumeKind(clarification.resume_kind)
            except ValueError as exc:
                raise QuestionUnderstandingError("CLARIFICATION_RESUME_KIND_INVALID") from exc
            previous_understanding = ctx.state.get("question_understanding")
            previous_understanding = previous_understanding if isinstance(previous_understanding, dict) else {}
            understanding_updated = False

            if resume_kind == AgentClarificationResumeKind.AGENT_TOOL:
                tool_call_id = clarification.tool_call_id or ""
                if not tool_call_id:
                    raise QuestionUnderstandingError("AGENT_TOOL_CLARIFICATION_CALL_ID_MISSING")
                messages.append(ToolMessage(content=answer_text, tool_call_id=tool_call_id))
                understanding = previous_understanding
            elif resume_kind == AgentClarificationResumeKind.QUESTION_UNDERSTANDING:
                outcome = apply_question_understanding_clarification(
                    understanding=previous_understanding,
                    resume_payload=clarification.resume_payload or {},
                    answer=clarification.answer or {},
                )
                understanding = outcome.model_dump(mode="json")
                ctx.state.update(
                    {
                        "question": outcome.rewritten_question,
                        "question_understanding": understanding,
                    }
                )
                understanding_updated = True
            else:  # pragma: no cover - 枚举构造已经覆盖所有合法类型。
                raise QuestionUnderstandingError("CLARIFICATION_RESUME_KIND_UNSUPPORTED")

            conversation_context = self._load_conversation_context(run, record)
            system = self._build_system(
                run,
                record,
                conversation_context=conversation_context,
                question_understanding=understanding,
            )
            agent_run_repository.update_run(
                self.session,
                run,
                status=AgentRunStatus.RUNNING.value,
                messages=_serialize_messages(messages),
                budget_snapshot=budget.snapshot(),
                derived_state=_persistable_state(ctx.state),
            )
            self.record_service.transition(
                record,
                ChatRecordStatus.RUNNING,
                execution_type=ChatRecordExecutionType.AGENT,
            )
            self.session.commit()
            yield self._emit(run, "clarification-accepted", {"record_id": record.id, "run_id": run.id})

            if understanding_updated:
                yield self._emit(
                    run,
                    "question-understood",
                    {
                        "record_id": record.id,
                        "message_type": understanding["message_type"],
                        "rewritten_question": understanding["rewritten_question"],
                        "intent_type": understanding["intent"]["intent_type"],
                        "confidence": understanding["intent"]["confidence"],
                        "validation": understanding["validation"],
                    },
                )
                preflight = self._understanding_preflight_clarification(understanding)
                if preflight is not None:
                    yield from self._suspend_for_preflight_clarification(
                        run,
                        record,
                        ctx,
                        messages,
                        budget,
                        preflight,
                    )
                    return
            yield from self._loop(run, record, ctx, system, messages, budget)
        except QuestionUnderstandingError as exc:
            yield from self._fail(
                run,
                record,
                messages,
                budget,
                str(exc),
                AgentErrorClass.UNDERSTANDING.value,
            )
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
        if run.id is None or record.id is None:
            raise RuntimeError("AGENT_EXECUTION_OWNERSHIP_MISSING")
        return AgentToolContext(
            session=self.session,
            oid=run.oid,
            user_id=self.current_user.id,
            datasource_id=record.datasource,
            execution_id=f"agent:{run.id}",
            chat_id=run.chat_id,
            record_id=record.id,
            dataset_id=record.dataset_id,
            term_query_service=self.term_query_service,
            query_service=self.query_service,
            semantic_query_service=self.semantic_query_service,
            semantic_retrieval_service=self.semantic_retrieval_service,
            physical_schema_service=self.physical_schema_service,
            result_artifact_service=self.result_artifact_service,
            config=self.config,
            state={"question": record.question or ""},
        )

    def _load_conversation_context(self, run: ChatbiAgentRun, record: ChatRecord) -> dict:
        history = agent_run_repository.recent_qa_summaries(self.session, run.chat_id, record.id, limit=self.config.history_rounds)
        previous_rewritten_question = agent_run_repository.latest_successful_rewritten_question(
            self.session,
            chat_id=run.chat_id,
            exclude_record_id=record.id,
            datasource_id=record.datasource,
        )
        return {
            "history": history,
            "last_rewritten_question": previous_rewritten_question,
        }

    def _build_system(
        self,
        run: ChatbiAgentRun,
        record: ChatRecord,
        *,
        conversation_context: dict,
        question_understanding: dict | None,
    ) -> SystemMessage:
        history = conversation_context.get("history") or []
        history_summary = None
        if history:
            history_summary = "\n".join(
                f"- 问：{item['question']}\n  SQL：{item['sql'] or '（无）'}\n  答（摘要）：{item['answer_brief']}"
                for item in history
            )
        return SystemMessage(
            content=build_system_prompt(
                datasource_id=record.datasource,
                oid=run.oid,
                max_clarifications=self.config.max_clarifications,
                history_summary=history_summary,
                question_understanding=question_understanding,
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
            step = agent_run_repository.start_step(self.session, run, step_index, None, {})
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
                agent_run_repository.finish_step(self.session, step, {"mode": "direct_answer"}, usage)
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
                step.args_summary = _tool_args_summary(tool_name, raw_args, ctx)
                self.session.add(step)
                yield self._emit(
                    run,
                    "tool-called",
                    {
                        "record_id": record.id,
                        "tool_name": tool_name,
                        "args_summary": step.args_summary,
                    },
                    step.id,
                )

                fuse = budget.check_tool_call(tool_name, raw_args)
                if not fuse.allowed:
                    agent_run_repository.fail_step(self.session, step, fuse.reason)
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
                        agent_run_repository.finish_step(self.session, step, {"tool": "clarify", "rejected": "budget"}, usage)
                        continue
                    agent_run_repository.finish_step(self.session, step, {"tool": "clarify"}, usage)
                    yield from self._suspend_for_clarification(
                        run,
                        record,
                        ctx,
                        messages,
                        budget,
                        output,
                        call_id,
                        step.id,
                        resume_kind=AgentClarificationResumeKind.AGENT_TOOL,
                        resume_payload={},
                    )
                    return

                messages.append(ToolMessage(content=output.summary, tool_call_id=call_id))

                if tool_name == "finish" and output.success:
                    agent_run_repository.finish_step(self.session, step, {"tool": "finish"}, usage)
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
                    agent_run_repository.finish_step(self.session, step, result_summary, usage)
                else:
                    agent_run_repository.fail_step(self.session, step, output.summary[:500])
                yield self._emit(run, "tool-result", {"record_id": record.id, "tool_name": tool_name, **result_summary}, step.id)
                for event_type, payload in _semantic_events(tool_name, output, record.id):
                    yield self._emit(run, event_type, payload, step.id)

                if tool_name == "execute_sql" and not output.success:
                    retry = budget.record_sql_failure()
                    if not retry.allowed:
                        yield from self._fail(run, record, messages, budget, retry.reason, retry.error_class)
                        return

            agent_run_repository.update_run(
                self.session, run,
                messages=_serialize_messages(messages),
                budget_snapshot=budget.snapshot(),
                derived_state=_persistable_state(ctx.state),
            )
            self.session.commit()

    # ---- 终态与挂起 ----

    @staticmethod
    def _understanding_preflight_clarification(understanding: dict) -> ToolOutput | None:
        """自然语言层已明确发现的维度歧义必须先澄清，不能被资产检索改变顺序。"""

        validation = understanding.get("validation") if isinstance(understanding, dict) else {}
        if not isinstance(validation, dict) or validation.get("status") != "clarification_required":
            return None
        reason_codes = set(validation.get("reason_codes") or [])
        intent = understanding.get("intent") if isinstance(understanding.get("intent"), dict) else {}
        dimension_slots = [
            slot
            for slot in intent.get("dimension_slots") or []
            if isinstance(slot, dict)
        ]
        if "dimension_role_ambiguous" in reason_codes:
            slot = next(
                (item for item in dimension_slots if str(item.get("role") or "").lower() == "ambiguous"),
                None,
            )
            if slot is None:
                return None
            name = str(slot.get("name") or "维度").strip() or "维度"
            options = [
                {"label": f"按{name}分组查看", "value": f"group_by:{name}"},
                {"label": f"筛选某个具体{name}", "value": f"filter:{name}"},
                {"label": f"不使用{name}维度，查看汇总结果", "value": f"ignore:{name}"},
            ]
            return ToolOutput(
                success=True,
                summary="clarify dimension role",
                payload={
                    "question": f"请确认“{name}”在本次查询中的使用方式。",
                    "options": options,
                    "reason": f"“{name}”可能表示分组维度、筛选条件或业务对象，需要先确认后再检索指标口径。",
                    "resume_payload": {
                        "operation": "set_dimension_role",
                        "slot_name": name,
                    },
                },
            )
        if "dimension_filter_value_missing" in reason_codes:
            slot = next(
                (item for item in dimension_slots if str(item.get("role") or "").lower() == "filter"),
                None,
            )
            if slot is None:
                return None
            name = str(slot.get("name") or "维度").strip() or "维度"
            return ToolOutput(
                success=True,
                summary="clarify dimension filter value",
                payload={
                    "question": f"请补充需要筛选的具体{name}。",
                    "options": [],
                    "reason": f"已确认{name}用于筛选，但还缺少具体筛选值。",
                    "resume_payload": {
                        "operation": "set_dimension_filter_value",
                        "slot_name": name,
                    },
                },
            )
        return None

    def _suspend_for_preflight_clarification(
        self,
        run,
        record,
        ctx,
        messages,
        budget,
        output: ToolOutput,
    ) -> Iterator[str]:
        """把确定性澄清记录成完整步骤，并在任何资产检索前挂起。"""

        verdict = budget.check_before_step()
        if not verdict.allowed:
            yield from self._fail(run, record, messages, budget, verdict.reason, verdict.error_class)
            return
        clarify_verdict = budget.record_clarification()
        if not clarify_verdict.allowed:
            yield from self._fail(
                run,
                record,
                messages,
                budget,
                clarify_verdict.reason,
                clarify_verdict.error_class,
            )
            return

        budget.record_system_step()
        step_index = budget.steps
        args_summary = {
            "question": output.payload["question"],
            "options": output.payload.get("options") or [],
            "source": "question_understanding",
        }
        reasoning_content = output.payload.get("reason") or "需要先澄清问题。"
        step = agent_run_repository.start_step(self.session, run, step_index, "understanding_clarification", args_summary)
        agent_run_repository.finish_step(
            self.session,
            step,
            {"action": "understanding_clarification", "source": "question_understanding"},
        )
        self.session.commit()
        yield self._emit(
            run,
            "step-started",
            {"record_id": record.id, "step_index": step_index},
            step.id,
        )
        yield self._emit(
            run,
            "thinking",
            {"record_id": record.id, "content": reasoning_content},
            step.id,
        )
        yield self._emit(
            run,
            "workflow-step",
            {
                "record_id": record.id,
                "action": "understanding_clarification",
                "args_summary": args_summary,
            },
            step.id,
        )
        yield from self._suspend_for_clarification(
            run,
            record,
            ctx,
            messages,
            budget,
            output,
            None,
            step.id,
            resume_kind=AgentClarificationResumeKind.QUESTION_UNDERSTANDING,
            resume_payload=output.payload["resume_payload"],
        )

    def _suspend_for_clarification(
        self,
        run,
        record,
        ctx,
        messages,
        budget,
        output: ToolOutput,
        call_id: str | None,
        step_id,
        *,
        resume_kind: AgentClarificationResumeKind,
        resume_payload: dict,
    ) -> Iterator[str]:
        clarification = agent_run_repository.create_clarification(
            self.session,
            run,
            question=output.payload["question"],
            options=output.payload.get("options") or [],
            tool_call_id=call_id,
            resume_kind=resume_kind.value,
            resume_payload=resume_payload,
            user_id=self.current_user.id,
        )
        self.record_service.transition(
            record,
            ChatRecordStatus.WAITING_USER,
            execution_type=ChatRecordExecutionType.AGENT,
        )
        agent_run_repository.update_run(
            self.session, run,
            status=AgentRunStatus.WAITING_USER.value,
            messages=_serialize_messages(messages),
            budget_snapshot=budget.snapshot(),
            derived_state=_persistable_state(ctx.state),
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
        record_data = None
        if full_data is not None and execution:
            record_payload = {
                "fields": execution.get("fields") or [],
                "data": full_data,
            }
            if execution.get("artifact_ref") is not None:
                record_payload["artifact_ref"] = execution["artifact_ref"]
            record_data = orjson.dumps(record_payload).decode()
        self.record_service.transition(
            record,
            ChatRecordStatus.SUCCEEDED,
            execution_type=ChatRecordExecutionType.AGENT,
            result=ChatRecordResultProjection(
                answer=answer,
                chart_answer=answer,
                sql=sql,
                chart=orjson.dumps(chart or {}).decode(),
                data=record_data,
            ),
        )
        agent_run_repository.update_run(
            self.session, run,
            status=AgentRunStatus.FINISHED.value,
            messages=_serialize_messages(messages),
            budget_snapshot=budget.snapshot(),
        )
        self.session.commit()
        yield self._emit(run, "answer", {"record_id": record.id, "content": answer}, step_id)
        yield self._emit(run, "run-finished", {"record_id": record.id, "content": answer}, step_id)
        yield self._emit(run, "finish", {"record_id": record.id, "content": answer}, step_id)

    def _fail(self, run, record, messages, budget, message, error_class) -> Iterator[str]:
        self.record_service.transition(
            record,
            ChatRecordStatus.FAILED,
            error=message,
            execution_type=ChatRecordExecutionType.AGENT,
        )
        agent_run_repository.update_run(
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
        event = agent_run_repository.append_trace(self.session, run.id, event_type, payload, step_id=step_id)
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


def _persistable_state(state: dict) -> dict:
    """可持久化的派生状态：排除全量数据这类大对象。"""

    return {key: value for key, value in state.items() if key != "full_data"}


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


def _tool_args_summary(tool_name: str, raw_args: dict, ctx: AgentToolContext) -> dict:
    """记录工具真正消费的业务输入，避免无参工具在轨迹中显示成空输入。"""

    if tool_name != "search_semantic_assets":
        return _args_summary(raw_args)
    understanding = ctx.state.get("question_understanding")
    if not isinstance(understanding, dict):
        return {}
    return _args_summary(
        {
            "rewritten_question": understanding.get("rewritten_question"),
            "intent": understanding.get("intent") or {},
        }
    )


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
