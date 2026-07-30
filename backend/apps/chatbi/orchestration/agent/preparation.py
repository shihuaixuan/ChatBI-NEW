"""Agent 首次执行与澄清恢复的输入准备流程。"""

from __future__ import annotations

import json
from collections.abc import Generator, Iterator
from typing import Any

from pydantic import BaseModel, Field

from apps.chatbi.errors import QuestionUnderstandingError, SemanticClarificationError
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
from apps.retrieval import (
    RetrievalBundle,
    RetrievalQueryError,
    RetrievalRequest,
    RetrievalResourceType,
    SemanticClarificationBinding,
    apply_decision_to_semantic_payload,
    apply_semantic_clarification,
    bind_default_time_dimensions,
    bundle_to_semantic_payload,
)
from apps.semantic.services.schema_service import DatasetSchemaProvider
from apps.tool.tools.semantic_contracts import build_semantic_compile_plan


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
        semantic_schema_provider: DatasetSchemaProvider,
        lifecycle: AgentLifecycle,
        event_publisher: EventPublisher,
    ) -> None:
        self._session = session
        self._config = config
        self._understanding_service = understanding_service
        self._semantic_schema_provider = semantic_schema_provider
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
            tenant_id=state.run.oid,
            dataset_id=record.dataset_id,
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
            tool_answer = answer_text
            if (clarification.resume_payload or {}).get("operation") == (
                "resolve_semantic_bindings"
            ):
                tool_answer = self._apply_semantic_clarification(
                    state,
                    clarification,
                    answer_text,
                )
            state.messages.append(AgentMessage.tool(tool_answer, tool_call_id))
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

    def _apply_semantic_clarification(
        self,
        state: AgentRuntimeState,
        clarification: ChatbiAgentClarification,
        answer_text: str,
    ) -> str:
        """验证用户选择并更新服务端可信语义决策。"""

        resume_payload = clarification.resume_payload or {}
        scope = state.context.state.get("semantic_scope")
        if not isinstance(scope, dict):
            raise SemanticClarificationError(
                SemanticClarificationError.SCOPE_REQUIRED
            )
        if resume_payload.get("retrieval_id") != scope.get("retrieval_id"):
            raise SemanticClarificationError(
                SemanticClarificationError.RETRIEVAL_MISMATCH
            )
        options = resume_payload.get("options")
        if not isinstance(options, list):
            raise SemanticClarificationError(
                SemanticClarificationError.OPTIONS_REQUIRED
            )
        option_by_value = {
            str(option["value"]): option
            for option in options
            if isinstance(option, dict) and option.get("value") is not None
        }
        answer = clarification.answer or {}
        selections = answer.get("selections")
        selected_values = (
            [
                str(item["value"])
                for item in selections
                if isinstance(item, dict) and item.get("value") is not None
            ]
            if isinstance(selections, list)
            else []
        )
        if not selected_values:
            raise SemanticClarificationError(
                SemanticClarificationError.STRUCTURED_SELECTION_REQUIRED
            )
        selected_options = []
        for value in selected_values:
            option = option_by_value.get(value)
            if option is None:
                raise SemanticClarificationError(
                    SemanticClarificationError.OPTION_NOT_FOUND
                )
            selected_options.append(option)
        bindings = [
            SemanticClarificationBinding.model_validate(binding)
            for option in selected_options
            for binding in option.get("bindings") or []
        ]

        raw_bundle = state.context.state.get("semantic_bundle")
        raw_request = state.context.state.get("semantic_retrieval_request")
        raw_payload = state.context.state.get("semantic_payload")
        if not isinstance(raw_bundle, dict) or not isinstance(raw_request, dict):
            raise SemanticClarificationError(
                SemanticClarificationError.SNAPSHOT_REQUIRED
            )
        if not isinstance(raw_payload, dict):
            raise SemanticClarificationError(
                SemanticClarificationError.PAYLOAD_REQUIRED
            )
        bundle = RetrievalBundle.model_validate(raw_bundle)
        request = RetrievalRequest.model_validate(raw_request)
        try:
            updated_bundle = apply_semantic_clarification(
                bundle,
                bindings,
                required_subquery_ids=_required_subquery_ids(
                    state.context.state.get("semantic_retrieval_filters")
                ),
            )
        except RetrievalQueryError as exc:
            reason_code = exc.details.get("reason_code") or str(exc)
            raise SemanticClarificationError(str(reason_code)) from exc
        try:
            time_range = request.intent.time_range
            if str(time_range.get("value_status") or "").lower() == "provided":
                # 指标在澄清后才收敛时，必须重新按指标模型绑定默认时间维度。
                schema = self._semantic_schema_provider.build_dataset_schema(
                    request.tenant_id,
                    request.scope.dataset_ids[0],
                )
                updated_bundle = bind_default_time_dimensions(
                    request,
                    updated_bundle,
                    schema,
                )
                updated_payload = bundle_to_semantic_payload(
                    request,
                    updated_bundle,
                    schema,
                )
            else:
                updated_payload = apply_decision_to_semantic_payload(
                    request,
                    raw_payload,
                    updated_bundle,
                )
        except RetrievalQueryError as exc:
            reason_code = exc.details.get("reason_code") or str(exc)
            raise SemanticClarificationError(str(reason_code)) from exc
        allowed_assets = [
            item.model_dump(mode="json")
            for item in updated_bundle.decision.allowed_asset_ids
        ]
        updated_scope = {
            **scope,
            "decision_status": updated_bundle.decision.status.value,
            "allowed_assets": allowed_assets,
            "compile_plan": build_semantic_compile_plan(
                updated_payload.get("slot_bindings") or {}
            ).model_dump(mode="json"),
        }
        state.context.state.update(
            {
                "semantic_bundle": updated_bundle.model_dump(mode="json"),
                "semantic_payload": updated_payload,
                "semantic_package": updated_payload,
                "semantic_scope": updated_scope,
                "semantic_asset_ids": sorted(
                    {int(item["asset_id"]) for item in allowed_assets}
                ),
            }
        )
        metric_ids = [
            item.asset_id
            for item in updated_bundle.decision.allowed_asset_ids
            if item.asset_type == RetrievalResourceType.METRIC
        ]
        dimension_ids = [
            item.asset_id
            for item in updated_bundle.decision.allowed_asset_ids
            if item.asset_type == RetrievalResourceType.DIMENSION
        ]
        return json.dumps(
            {
                "user_answer": answer_text,
                "semantic_binding": {
                    "decision_status": updated_bundle.decision.status.value,
                    "metric_asset_ids": metric_ids,
                    "dimension_asset_ids": dimension_ids,
                    "slot_bindings": updated_payload.get("slot_bindings") or {},
                    "remaining_ambiguities": _remaining_ambiguity_summary(
                        updated_payload
                    ),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )

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


def _required_subquery_ids(raw_filters: Any) -> set[str] | None:
    if not isinstance(raw_filters, dict):
        return None
    subqueries = raw_filters.get("subqueries")
    if not isinstance(subqueries, list):
        return None
    return {
        str(item["subquery_id"])
        for item in subqueries
        if isinstance(item, dict)
        and item.get("required") is True
        and item.get("subquery_id")
    }


def _remaining_ambiguity_summary(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for ambiguity in payload.get("ambiguities") or []:
        if not isinstance(ambiguity, dict):
            continue
        candidates = [
            {
                key: candidate.get(key)
                for key in (
                    "asset_type",
                    "asset_id",
                    "model_id",
                    "display_name",
                    "biz_name",
                )
                if candidate.get(key) is not None
            }
            for candidate in ambiguity.get("candidates") or []
            if isinstance(candidate, dict)
        ]
        result.append(
            {
                "subquery_id": ambiguity.get("subquery_id"),
                "type": ambiguity.get("type"),
                "candidates": candidates,
            }
        )
    return result


__all__ = ["AgentInputPreparer"]
