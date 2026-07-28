"""Agent 首次执行与澄清恢复的输入准备流程。"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from typing import Any

from pydantic import BaseModel, Field

from apps.chatbi.errors import QuestionUnderstandingError
from apps.chatbi.models import (
    AgentClarificationResumeKind,
    AgentErrorClass,
    ChatbiAgentClarification,
)
from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.messages import AgentMessage, restore_messages
from apps.chatbi.orchestration.agent.prompts import build_system_prompt
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.understanding import (
    QuestionUnderstandingService,
    apply_question_understanding_clarification,
)
from apps.event import EventPublisher, RenderEvent


class PreflightClarification(BaseModel):
    """问题理解阶段产生的确定性澄清请求。"""

    question: str
    options: list[dict[str, Any]] = Field(default_factory=list)
    reason: str
    resume_payload: dict[str, Any]


class AgentInputPreparer:
    """准备进入 ReAct 循环所需的问题、消息、上下文和系统提示。"""

    def __init__(
        self,
        session: Any,
        config: AgentConfig,
        understanding_service: QuestionUnderstandingService,
        lifecycle: AgentLifecycle,
        event_publisher: EventPublisher,
    ) -> None:
        self._session = session
        self._config = config
        self._understanding_service = understanding_service
        self._lifecycle = lifecycle
        self._event_publisher = event_publisher

    def prepare_initial(
        self,
        state: AgentRuntimeState,
    ) -> Generator[RenderEvent, None, bool]:
        """完成首次问题理解；返回值表示是否可以进入 Agent 主循环。"""

        record = state.record
        conversation_context = self._load_conversation_context(state)
        outcome = self._understanding_service.understand(
            question=record.question or "",
            datasource_id=record.datasource,
            conversation_context={
                "last_rewritten_question": conversation_context.get(
                    "last_rewritten_question"
                ),
            },
        )
        state.budget.record_llm_usage(outcome.usage_metadata)
        understanding = outcome.output.model_dump(mode="json")
        state.context.state.update(
            {
                "original_question": record.question or "",
                "question": outcome.output.rewritten_question,
                "question_understanding": understanding,
            }
        )
        state.messages = [AgentMessage.user(outcome.output.rewritten_question)]
        state.system = self._build_system(
            state,
            conversation_context=conversation_context,
            question_understanding=understanding,
        )
        self._persist_snapshot(state)
        yield self._question_understood_event(state, understanding)

        preflight = self._preflight_clarification(understanding)
        if preflight is not None:
            yield from self._suspend_for_preflight_clarification(state, preflight)
            return False
        return True

    def prepare_resume(
        self,
        state: AgentRuntimeState,
        clarification: ChatbiAgentClarification,
        answer_text: str,
    ) -> Generator[RenderEvent, None, bool]:
        """恢复持久化状态并合并用户答案；返回值表示是否可以继续主循环。"""

        run = state.run
        state.budget.restore(run.budget_snapshot)
        state.chatbi_budget.restore(run.budget_snapshot)
        # 恢复挂起前的派生状态，避免重复检索已经获得的语义资产。
        state.context.state.update(run.derived_state or {})
        state.messages = restore_messages(run.messages)

        try:
            resume_kind = AgentClarificationResumeKind(clarification.resume_kind)
        except ValueError as exc:
            raise QuestionUnderstandingError("CLARIFICATION_RESUME_KIND_INVALID") from exc

        previous_understanding = state.context.state.get("question_understanding")
        previous_understanding = (
            previous_understanding
            if isinstance(previous_understanding, dict)
            else {}
        )
        understanding_updated = False

        if resume_kind == AgentClarificationResumeKind.AGENT_TOOL:
            tool_call_id = clarification.tool_call_id or ""
            if not tool_call_id:
                raise QuestionUnderstandingError(
                    "AGENT_TOOL_CLARIFICATION_CALL_ID_MISSING"
                )
            state.messages.append(
                AgentMessage.tool(answer_text, tool_call_id)
            )
            understanding = previous_understanding
        elif resume_kind == AgentClarificationResumeKind.QUESTION_UNDERSTANDING:
            outcome = apply_question_understanding_clarification(
                understanding=previous_understanding,
                resume_payload=clarification.resume_payload or {},
                answer=clarification.answer or {},
            )
            understanding = outcome.model_dump(mode="json")
            state.context.state.update(
                {
                    "question": outcome.rewritten_question,
                    "question_understanding": understanding,
                }
            )
            understanding_updated = True
        else:  # pragma: no cover - 枚举构造已经覆盖所有合法类型。
            raise QuestionUnderstandingError("CLARIFICATION_RESUME_KIND_UNSUPPORTED")

        state.system = self._build_system(
            state,
            conversation_context=self._load_conversation_context(state),
            question_understanding=understanding,
        )
        yield self._lifecycle.resume(state)

        if understanding_updated:
            yield self._question_understood_event(state, understanding)
            preflight = self._preflight_clarification(understanding)
            if preflight is not None:
                yield from self._suspend_for_preflight_clarification(state, preflight)
                return False
        return True

    def _persist_snapshot(self, state: AgentRuntimeState) -> None:
        agent_run_repository.update_run(
            self._session,
            state.run,
            messages=state.serialized_messages(),
            budget_snapshot=state.budget_snapshot(),
            derived_state=state.persistable_context(),
        )
        self._session.commit()

    def _load_conversation_context(self, state: AgentRuntimeState) -> dict[str, Any]:
        run = state.run
        record = state.record
        history = agent_run_repository.recent_qa_summaries(
            self._session,
            run.chat_id,
            record.id,
            limit=self._config.history_rounds,
        )
        previous_rewritten_question = (
            agent_run_repository.latest_successful_rewritten_question(
                self._session,
                chat_id=run.chat_id,
                exclude_record_id=record.id,
                datasource_id=record.datasource,
            )
        )
        return {
            "history": history,
            "last_rewritten_question": previous_rewritten_question,
        }

    def _build_system(
        self,
        state: AgentRuntimeState,
        *,
        conversation_context: dict[str, Any],
        question_understanding: dict[str, Any] | None,
    ) -> AgentMessage:
        history = conversation_context.get("history") or []
        history_summary = None
        if history:
            history_summary = "\n".join(
                f"- 问：{item['question']}\n  SQL：{item['sql'] or '（无）'}\n  答（摘要）：{item['answer_brief']}"
                for item in history
            )
        return AgentMessage.system(
            build_system_prompt(
                datasource_id=state.record.datasource,
                oid=state.run.oid,
                max_clarifications=self._config.max_clarifications,
                history_summary=history_summary,
                question_understanding=question_understanding,
            )
        )

    @staticmethod
    def _preflight_clarification(
        understanding: dict[str, Any],
    ) -> PreflightClarification | None:
        """自然语言层已发现的维度歧义必须在资产检索前澄清。"""

        validation = understanding.get("validation")
        if not isinstance(validation, dict) or validation.get("status") != "clarification_required":
            return None
        reason_codes = set(validation.get("reason_codes") or [])
        intent = understanding.get("intent")
        intent = intent if isinstance(intent, dict) else {}
        dimension_slots = [
            slot for slot in intent.get("dimension_slots") or [] if isinstance(slot, dict)
        ]
        if "dimension_role_ambiguous" in reason_codes:
            slot = next(
                (
                    item
                    for item in dimension_slots
                    if str(item.get("role") or "").lower() == "ambiguous"
                ),
                None,
            )
            if slot is None:
                return None
            name = str(slot.get("name") or "维度").strip() or "维度"
            return PreflightClarification(
                question=f"请确认“{name}”在本次查询中的使用方式。",
                options=[
                        {"label": f"按{name}分组查看", "value": f"group_by:{name}"},
                        {"label": f"筛选某个具体{name}", "value": f"filter:{name}"},
                        {
                            "label": f"不使用{name}维度，查看汇总结果",
                            "value": f"ignore:{name}",
                        },
                    ],
                reason=f"“{name}”可能表示分组维度、筛选条件或业务对象，需要先确认后再检索指标口径。",
                resume_payload={
                    "operation": "set_dimension_role",
                    "slot_name": name,
                },
            )
        if "dimension_filter_value_missing" in reason_codes:
            slot = next(
                (
                    item
                    for item in dimension_slots
                    if str(item.get("role") or "").lower() == "filter"
                ),
                None,
            )
            if slot is None:
                return None
            name = str(slot.get("name") or "维度").strip() or "维度"
            return PreflightClarification(
                question=f"请补充需要筛选的具体{name}。",
                options=[],
                reason=f"已确认{name}用于筛选，但还缺少具体筛选值。",
                resume_payload={
                    "operation": "set_dimension_filter_value",
                    "slot_name": name,
                },
            )
        return None

    def _suspend_for_preflight_clarification(
        self,
        state: AgentRuntimeState,
        output: PreflightClarification,
    ) -> Iterator[RenderEvent]:
        """把问题理解产生的确定性澄清记录成完整步骤。"""

        verdict = state.budget.check_before_step()
        if not verdict.allowed:
            yield from self._lifecycle.fail(
                state,
                verdict.reason or "预算已耗尽",
                verdict.error_class or AgentErrorClass.BUDGET.value,
            )
            return
        clarify_verdict = state.chatbi_budget.record_clarification()
        if not clarify_verdict.allowed:
            yield from self._lifecycle.fail(
                state,
                clarify_verdict.reason or "澄清次数已达上限",
                clarify_verdict.error_class or AgentErrorClass.BUDGET.value,
            )
            return

        state.budget.record_system_step()
        step_index = state.budget.steps
        args_summary = {
            "question": output.question,
            "options": output.options,
            "source": "question_understanding",
        }
        reasoning_content = output.reason or "需要先澄清问题。"
        step = agent_run_repository.start_step(
            self._session,
            state.run,
            step_index,
        )
        agent_run_repository.finish_step(
            self._session,
            step,
            {
                "action": "understanding_clarification",
                "source": "question_understanding",
            },
        )
        self._session.commit()
        yield self._publish(
            state,
            "step-started",
            {
                "record_id": state.record.id,
                "step_id": step.id,
                "step_index": step_index,
            },
            step.id,
        )
        yield self._publish(
            state,
            "thinking",
            {"record_id": state.record.id, "content": reasoning_content},
            step.id,
        )
        yield self._publish(
            state,
            "workflow-step",
            {
                "record_id": state.record.id,
                "action": "understanding_clarification",
                "args_summary": args_summary,
            },
            step.id,
        )
        yield self._lifecycle.suspend(
            state,
            output.question,
            output.options,
            None,
            step.id,
            resume_kind=AgentClarificationResumeKind.QUESTION_UNDERSTANDING,
            resume_payload=output.resume_payload,
        )

    def _question_understood_event(
        self,
        state: AgentRuntimeState,
        understanding: dict[str, Any],
    ) -> RenderEvent:
        return self._publish(
            state,
            "question-understood",
            {
                "record_id": state.record.id,
                "message_type": understanding["message_type"],
                "rewritten_question": understanding["rewritten_question"],
                "intent_type": understanding["intent"]["intent_type"],
                "confidence": understanding["intent"]["confidence"],
                "validation": understanding["validation"],
            },
        )

    def _publish(
        self,
        state: AgentRuntimeState,
        event_type: str,
        payload: dict[str, Any],
        step_id: int | None = None,
    ) -> RenderEvent:
        event = self._event_publisher.publish(
            state.require_run_id(),
            event_type,
            payload,
            step_id=step_id,
        )
        self._session.commit()
        return event


__all__ = ["AgentInputPreparer"]
