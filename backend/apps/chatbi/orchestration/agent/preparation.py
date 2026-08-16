"""Agent 首次执行与澄清恢复的输入准备流程。"""

from __future__ import annotations

import json
from collections.abc import Generator, Iterator
from typing import Any, cast

from apps.chatbi.errors import QuestionUnderstandingError, SemanticClarificationError
from apps.chatbi.models import (
    AgentClarificationResumeKind,
    AgentErrorClass,
    ChatbiAgentClarification,
)
from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.messages import AgentMessage, restore_messages
from apps.chatbi.orchestration.agent.prompts import (
    build_runtime_context,
    build_system_prompt,
)
from apps.chatbi.orchestration.agent.semantic_projection import (
    refresh_semantic_projection,
)
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.generation.capability_answer import build_capability_answer
from apps.chatbi.services.understanding import (
    ClarificationCard,
    ClarificationRefusal,
    QuestionUnderstandingService,
    apply_question_understanding_clarification,
    evaluate_clarification,
)
from apps.event import EventPublisher, RenderEvent
from apps.memory import ClarificationMemoryEvent, MemoryService
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
from apps.temporal import resolve_time_range
from apps.tool.tools.semantic_contracts import (
    project_semantic_compile_plan,
    project_semantic_query_plan,
)
from apps.trace import (
    AgentTraceRecorder,
    TraceNodeSpec,
    TraceNodeStatus,
    TraceNodeType,
)
from common.core.config import settings


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
        trace_recorder: AgentTraceRecorder,
        memory_service: MemoryService | None = None,
    ) -> None:
        self._session = session
        self._config = config
        self._understanding_service = understanding_service
        self._semantic_schema_provider = semantic_schema_provider
        self._lifecycle = lifecycle
        self._event_publisher = event_publisher
        self._trace_recorder = trace_recorder
        self._memory_service = memory_service

    def prepare_initial(
        self,
        state: AgentRuntimeState,
    ) -> Generator[RenderEvent, None, bool]:
        """完成首次问题理解；返回值表示是否可以进入 Agent 主循环。"""

        record = state.record
        run_id = state.require_run_id()
        with self._trace_recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key="phase:question_understanding",
                node_type=TraceNodeType.PHASE,
                name="question_understanding",
                display_name="问题理解",
            ),
            input_data={
                "datasource_id": record.datasource,
                "dataset_id": record.dataset_id,
            },
            input_detail={"original_question": record.question or ""},
        ) as understanding_node:
            with self._trace_recorder.node(
                TraceNodeSpec(
                    run_id=run_id,
                    node_type=TraceNodeType.PHASE,
                    name="load_conversation_context",
                    display_name="读取会话上下文",
                ),
                input_data={"history_rounds": self._config.history_rounds},
            ) as context_node:
                conversation_context = self._load_conversation_context(state)
                context_node.set_output(
                    {
                        "history_count": len(conversation_context.get("history") or []),
                        "has_last_rewritten_question": bool(
                            conversation_context.get("last_rewritten_question")
                        ),
                        "has_previous_understanding": bool(
                            conversation_context.get("previous_understanding")
                        ),
                    }
                )
                context_node.set_output_detail(
                    {"conversation_context": conversation_context}
                )
            understanding_context = {
                "last_rewritten_question": conversation_context.get(
                    "last_rewritten_question"
                ),
                "previous_understanding": conversation_context.get(
                    "previous_understanding"
                ),
            }
            if conversation_context.get("memory_context"):
                understanding_context["user_memory"] = conversation_context[
                    "memory_context"
                ]
            outcome = self._understanding_service.understand(
                question=record.question or "",
                datasource_id=record.datasource,
                tenant_id=state.run.oid,
                dataset_id=record.dataset_id,
                temporal_context=state.temporal_context,
                conversation_context=understanding_context,
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
            if outcome.temporal_shadow is not None:
                # 旁路结果只用于后续评估，不参与检索、计划或 SQL。
                state.context.state["temporal_shadow_observation"] = (
                    outcome.temporal_shadow.model_dump(mode="json")
                )
            state.messages = [AgentMessage.user(outcome.output.rewritten_question)]
            state.system = self._build_system(
                state,
                conversation_context=conversation_context,
                question_understanding=understanding,
            )
            understanding_node.set_output(
                {
                    "message_type": outcome.output.message_type,
                    "intent_type": outcome.output.intent.intent_type,
                    "validation_status": outcome.output.validation.status,
                }
            )
            understanding_node.set_output_detail(
                {"question_understanding": understanding}
            )
            with self._trace_recorder.node(
                TraceNodeSpec(
                    run_id=run_id,
                    node_type=TraceNodeType.PERSISTENCE,
                    name="persist_understanding_snapshot",
                    display_name="持久化问题理解快照",
                ),
                input_data={"validation_status": outcome.output.validation.status},
            ) as persistence_node:
                self._persist_snapshot(state)
                persistence_node.set_output(
                    {
                        "message_count": len(state.messages),
                        "derived_state_keys": sorted(state.context.state),
                    }
                )
            yield self._question_understood_event(state, understanding)

            triage = self._triage_without_query(state, understanding)
            if triage is not None:
                understanding_node.set_output({"category": _category_of(understanding)})
                yield from triage
                return False

            preflight = evaluate_clarification(understanding)
            if isinstance(preflight, ClarificationCard):
                understanding_node.set_status(TraceNodeStatus.WAITING)
                yield from self._suspend_for_preflight_clarification(state, preflight)
                return False
            if isinstance(preflight, ClarificationRefusal):
                yield from self._refuse_question(state, preflight)
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
        run_id = state.require_run_id()
        clarification_id = getattr(clarification, "id", None)
        with self._trace_recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"restore_checkpoint:{clarification_id or 'pending'}",
                node_type=TraceNodeType.PHASE,
                name="restore_agent_checkpoint",
                display_name="恢复 Agent 挂起快照",
                metadata={"clarification_id": clarification_id},
            ),
            input_data={
                "saved_message_count": len(run.messages or []),
                "saved_state_key_count": len(run.derived_state or {}),
            },
        ) as restore_node:
            state.budget.restore(run.budget_snapshot)
            state.chatbi_budget.restore(run.budget_snapshot)
            # 恢复挂起前的派生状态，避免重复检索已经获得的语义资产。
            state.context.state.update(run.derived_state or {})
            state.messages = restore_messages(run.messages)

            try:
                resume_kind = AgentClarificationResumeKind(clarification.resume_kind)
            except ValueError as exc:
                raise QuestionUnderstandingError(
                    "CLARIFICATION_RESUME_KIND_INVALID"
                ) from exc
            restore_node.set_output(
                {
                    "resume_kind": resume_kind.value,
                    "message_count": len(state.messages),
                    "state_keys": sorted(state.context.state),
                }
            )
            restore_node.set_output_detail(
                {
                    "budget_snapshot": state.budget_snapshot(),
                    "derived_state": state.persistable_context(),
                    "messages": state.serialized_messages(),
                }
            )

        previous_understanding = state.context.state.get("question_understanding")
        previous_understanding = (
            previous_understanding if isinstance(previous_understanding, dict) else {}
        )
        understanding_updated = False
        before_projection = {
            "message_count": len(state.messages),
            "state_keys": sorted(state.context.state),
            "state_revision": int(state.context.state.get("state_revision") or 0),
        }
        with self._trace_recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"clarification_projection:{clarification_id or 'pending'}",
                node_type=TraceNodeType.PROJECTION,
                name="apply_clarification_answer",
                display_name="校验并合并用户澄清答案",
                metadata={
                    "clarification_id": clarification_id,
                    "resume_kind": resume_kind.value,
                },
            ),
            input_data={
                "resume_kind": resume_kind.value,
                "operation": (clarification.resume_payload or {}).get("operation"),
            },
            input_detail={
                "answer_text": answer_text,
                "structured_answer": clarification.answer or {},
                "resume_payload": clarification.resume_payload or {},
            },
        ) as projection_node:
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
                resume_payload = clarification.resume_payload or {}
                if resume_payload.get("operation") == "resolve_time_range":
                    understanding = self._apply_time_range_clarification(
                        state,
                        previous_understanding,
                        clarification,
                        answer_text,
                    )
                    state.context.state.update(
                        {
                            "question": understanding.get("rewritten_question"),
                            "question_understanding": understanding,
                            "time_parse_status": "resolved",
                        }
                    )
                    understanding_updated = True
                else:
                    if resume_payload.get("operation") == "resolve_temporal_plan":
                        resolved_outcome = (
                            self._understanding_service.resolve_temporal_clarification(
                                understanding=previous_understanding,
                                answer=clarification.answer or {},
                                temporal_context=state.temporal_context,
                            )
                        )
                        state.budget.record_llm_usage(resolved_outcome.usage_metadata)
                        updated_output = resolved_outcome.output
                    else:
                        updated_output = apply_question_understanding_clarification(
                            understanding=previous_understanding,
                            resume_payload=resume_payload,
                            answer=clarification.answer or {},
                            temporal_context=state.temporal_context,
                        )
                    understanding = updated_output.model_dump(mode="json")
                    state.context.state.update(
                        {
                            "question": updated_output.rewritten_question,
                            "question_understanding": understanding,
                        }
                    )
                    understanding_updated = True
            else:  # pragma: no cover - 枚举构造已经覆盖所有合法类型。
                raise QuestionUnderstandingError(
                    "CLARIFICATION_RESUME_KIND_UNSUPPORTED"
                )
            after_projection = {
                "message_count": len(state.messages),
                "state_keys": sorted(state.context.state),
                "state_revision": int(
                    state.context.state.get("state_revision") or 0
                ),
            }
            projection_node.set_output(
                {
                    "resume_kind": resume_kind.value,
                    "understanding_updated": understanding_updated,
                    "message_count": len(state.messages),
                }
            )
            projection_node.set_state_diff(before_projection, after_projection)
            projection_node.set_output_detail(
                {
                    "question_understanding": understanding,
                    "derived_state": state.persistable_context(),
                }
            )

        self._record_clarification_memory(state, clarification, answer_text)

        with self._trace_recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"rebuild_context:{clarification_id or 'pending'}",
                node_type=TraceNodeType.PHASE,
                name="rebuild_agent_context",
                display_name="重建恢复后的 Agent 上下文",
                metadata={"clarification_id": clarification_id},
            ),
            input_data={"understanding_updated": understanding_updated},
        ) as rebuild_node:
            conversation_context = self._load_conversation_context(state)
            state.system = self._build_system(
                state,
                conversation_context=conversation_context,
                question_understanding=understanding,
            )
            rebuild_node.set_output(
                {
                    "history_count": len(conversation_context.get("history") or []),
                    "system_prompt_ready": state.system is not None,
                }
            )
            rebuild_node.set_output_detail(
                {
                    "conversation_context": conversation_context,
                    "system_message": state.system.model_dump(mode="json"),
                }
            )
        yield self._lifecycle.resume(state)

        pending_clarifications = (clarification.resume_payload or {}).get(
            "pending_clarifications"
        )
        if isinstance(pending_clarifications, list) and pending_clarifications:
            # 当前回答已经写入状态，先切换回运行态，再创建队列中的下一张卡片。
            next_card = pending_clarifications[0]
            remaining_cards = pending_clarifications[1:]
            if not isinstance(next_card, dict):
                raise QuestionUnderstandingError("CLARIFICATION_QUEUE_ITEM_INVALID")
            next_payload = next_card.get("resume_payload")
            if not isinstance(next_payload, dict):
                raise QuestionUnderstandingError("CLARIFICATION_QUEUE_PAYLOAD_INVALID")
            next_payload = dict(next_payload)
            if remaining_cards:
                next_payload["pending_clarifications"] = remaining_cards
            clarify_verdict = state.chatbi_budget.record_clarification()
            if not clarify_verdict.allowed:
                yield from self._lifecycle.fail(
                    state,
                    clarify_verdict.reason or "澄清次数已达上限",
                    clarify_verdict.error_class or AgentErrorClass.BUDGET.value,
                )
                return False
            yield self._lifecycle.suspend(
                state,
                str(next_card.get("question") or "请补充必要信息。"),
                list(next_card.get("options") or []),
                next_card.get("tool_call_id"),
                None,
                resume_kind=AgentClarificationResumeKind(
                    str(
                        next_card.get("resume_kind")
                        or AgentClarificationResumeKind.QUESTION_UNDERSTANDING.value
                    )
                ),
                resume_payload=next_payload,
            )
            return False

        if understanding_updated:
            yield self._question_understood_event(state, understanding)
            preflight = evaluate_clarification(understanding)
            if isinstance(preflight, ClarificationCard):
                yield from self._suspend_for_preflight_clarification(state, preflight)
                return False
            if isinstance(preflight, ClarificationRefusal):
                yield from self._refuse_question(state, preflight)
                return False
        return True

    def _apply_time_range_clarification(
        self,
        state: AgentRuntimeState,
        previous_understanding: dict[str, Any],
        clarification: ChatbiAgentClarification,
        answer_text: str,
    ) -> dict[str, Any]:
        """解析用户补充的明确时间，并回填问题理解。"""

        answer = clarification.answer or {}
        raw = str(answer.get("text") or answer_text).strip()
        normalized = resolve_time_range(raw, state.temporal_context)
        if not isinstance(normalized, dict) or normalized.get("kind") == "unsupported":
            raise QuestionUnderstandingError("TIME_RANGE_CLARIFICATION_UNSUPPORTED")
        understanding = cast(
            dict[str, Any],
            json.loads(json.dumps(previous_understanding, ensure_ascii=False)),
        )
        intent = understanding.get("intent")
        if not isinstance(intent, dict):
            raise QuestionUnderstandingError("TIME_RANGE_CLARIFICATION_INTENT_MISSING")
        time_range = dict(intent.get("time_range") or {})
        time_range.update(
            {
                "raw": raw,
                "value_status": "provided",
                "normalized": normalized,
                "interpretation_source": "user_confirmation",
            }
        )
        intent["time_range"] = time_range
        package = state.context.state.get("semantic_package")
        scope = state.context.state.get("semantic_scope")
        if isinstance(package, dict) and isinstance(scope, dict):
            schema_data = state.context.state.get("semantic_schema")
            if scope.get("semantic_enforcement") == "STRICT" and not isinstance(
                schema_data, dict
            ):
                schema_data = self._semantic_schema_provider.build_dataset_schema(
                    scope["workspace_id"],
                    scope["dataset_id"],
                ).model_dump(mode="json")
            (
                refreshed_package,
                refreshed_scope,
                snapshot_patch,
            ) = refresh_semantic_projection(
                {
                    **state.context.state,
                    "question_understanding": understanding,
                },
                package,
                scope,
                schema_data=schema_data,
            )
            state.context.state.update(
                {
                    "semantic_package": refreshed_package,
                    "semantic_scope": refreshed_scope,
                    **snapshot_patch,
                }
            )
        return understanding

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
            raise SemanticClarificationError(SemanticClarificationError.SCOPE_REQUIRED)
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
            "compile_plan": project_semantic_compile_plan(
                updated_payload.get("slot_bindings") or {},
                (
                    state.context.state.get("question_understanding", {}).get("intent")
                    if isinstance(
                        state.context.state.get("question_understanding"), dict
                    )
                    else {}
                ),
            ).model_dump(mode="json"),
        }
        if scope.get("semantic_enforcement") == "STRICT":
            schema = self._semantic_schema_provider.build_dataset_schema(
                request.tenant_id,
                request.scope.dataset_ids[0],
            )
            try:
                query_plan, validation_report = project_semantic_query_plan(
                    schema,
                    updated_payload.get("slot_bindings") or {},
                    (
                        state.context.state.get("question_understanding", {}).get("intent")
                        if isinstance(
                            state.context.state.get("question_understanding"), dict
                        )
                        else {}
                    ),
                )
            except ValueError as exc:
                raise SemanticClarificationError(str(exc)) from exc
            updated_scope.update(
                {
                    "query_plan": query_plan.model_dump(mode="json"),
                    "validation_report": validation_report.model_dump(mode="json"),
                }
            )
        state.context.state.update(
            {
                "semantic_bundle": updated_bundle.model_dump(mode="json"),
                "semantic_payload": updated_payload,
                "semantic_package": updated_payload,
                "semantic_scope": updated_scope,
                "semantic_asset_ids": sorted(
                    {int(item["asset_id"]) for item in allowed_assets}
                ),
                # 澄清改变语义绑定后，旧 SQL 和执行结果不再属于当前可信计划。
                "compiled_sql": None,
                "validated_sql": None,
                "last_execution": None,
                "full_data": None,
            }
        )
        self._apply_canonical_values_to_understanding(state, updated_payload)
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
        previous_understanding = agent_run_repository.latest_successful_question_understanding(
            self._session,
            chat_id=run.chat_id,
            exclude_record_id=record.id,
            datasource_id=record.datasource,
        )
        previous_rewritten_question = (
            previous_understanding.get("rewritten_question")
            if previous_understanding
            else None
        )
        return {
            "history": history,
            "last_rewritten_question": previous_rewritten_question,
            "previous_understanding": previous_understanding,
            "memory_context": self._load_memory_context(state),
        }

    def _load_memory_context(self, state: AgentRuntimeState) -> dict[str, Any]:
        if self._memory_service is None:
            return {}
        record = state.record
        run = state.run
        user_id = getattr(record, "create_by", None) or run.created_by
        if user_id is None:
            return {}
        resolved_user_id = int(user_id)
        assignment = self._memory_service.assign_recall_variant(
            run.oid,
            resolved_user_id,
        )
        context = self._memory_service.build_context(
            run.oid,
            resolved_user_id,
            query_text=str(record.question or ""),
            recall_variant=assignment.recall_variant,
        )
        self._memory_service.record_usage(
            run.oid,
            resolved_user_id,
            context,
            stage="agent_context",
            run_id=str(run.id) if run.id is not None else None,
            session_id=str(run.chat_id),
            recall_variant=assignment.recall_variant,
        )
        return context.model_dump(mode="json")

    def _apply_canonical_values_to_understanding(
        self,
        state: AgentRuntimeState,
        payload: dict[str, Any],
    ) -> None:
        """澄清选中维值后，把 canonical 值写回问题理解槽位，保持后续口径一致。"""

        slot_bindings = payload.get("slot_bindings")
        normalizations: list[dict[str, Any]] = []
        for item in (
            slot_bindings.get("value_filters") if isinstance(slot_bindings, dict) else None
        ) or []:
            if not isinstance(item, dict):
                continue
            dimension_names = {
                str(item.get("display_name") or "").casefold(),
                str(item.get("biz_name") or "").casefold(),
            }
            for entry in item.get("value_normalizations") or []:
                if isinstance(entry, dict) and entry.get("original_term"):
                    normalizations.append(
                        {
                            "dimension_names": {
                                name for name in dimension_names if name
                            },
                            "original": str(entry["original_term"]),
                            "canonical": str(entry.get("canonical_value") or ""),
                        }
                    )
        if not normalizations:
            return
        understanding = state.context.state.get("question_understanding")
        if not isinstance(understanding, dict):
            return
        intent = understanding.get("intent")
        slots = intent.get("dimension_slots") if isinstance(intent, dict) else None
        if not isinstance(slots, list):
            return
        changed = False
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            if str(slot.get("role") or "").lower() != "filter":
                continue
            slot_name = str(slot.get("name") or "").casefold()
            values = slot.get("value")
            items = values if isinstance(values, list) else [values]
            replaced: list[Any] = []
            slot_changed = False
            for item in items:
                matched = next(
                    (
                        normalization
                        for normalization in normalizations
                        if slot_name in normalization["dimension_names"]
                        and isinstance(item, str)
                        and item.casefold() == normalization["original"].casefold()
                        and normalization["canonical"]
                    ),
                    None,
                )
                if matched is not None:
                    replaced.append(matched["canonical"])
                    slot_changed = True
                else:
                    replaced.append(item)
            if slot_changed:
                slot["value"] = replaced if isinstance(values, list) else replaced[0]
                changed = True
        if changed:
            state.context.state["value_normalization_applied"] = True

    def _record_clarification_memory(
        self,
        state: AgentRuntimeState,
        clarification: ChatbiAgentClarification,
        answer_text: str,
    ) -> None:
        """只提交已经通过当前澄清校验的用户明确偏好。"""

        if self._memory_service is None or state.context.user_id is None:
            return
        question = str(clarification.question or "").strip()
        answer = answer_text.strip()
        if not question or not answer:
            return
        self._memory_service.record_clarification(
            state.run.oid,
            state.context.user_id,
            ClarificationMemoryEvent(
                question=question,
                answer_text=answer,
                source_ref=(
                    str(getattr(clarification, "id", None))
                    if getattr(clarification, "id", None) is not None
                    else None
                ),
                source_session_id=str(state.run.chat_id),
            ),
        )

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
        state.runtime_context = AgentMessage.user(
            build_runtime_context(
                history_summary=history_summary,
                question_understanding=question_understanding,
                memory_context=conversation_context.get("memory_context"),
            )
        )
        return AgentMessage.system(
            build_system_prompt(max_clarifications=self._config.max_clarifications)
        )

    def _triage_without_query(
        self,
        state: AgentRuntimeState,
        understanding: dict[str, Any],
    ) -> Iterator[RenderEvent] | None:
        """非问数分诊的确定性收口：meta 走资产目录、越界拒答；闲聊交给主循环直答。"""

        if not settings.CHATBI_TRIAGE_ENABLED:
            return None
        category = _category_of(understanding)
        if category == "meta_query":
            answer = self._capability_answer(state)
            return self._finish_without_query(
                state,
                answer=answer,
                name="answer_meta_query",
                display_name="资产目录作答",
                category=category,
            )
        if category == "out_of_scope":
            answer = (
                "当前问题超出我可以回答的范围：我只在管理员配置的数据集内做取数与分析，"
                "不支持预测推演、修改数据或数据集之外的自由问答。"
                "可以试试：“本月的总销售额是多少？”这类问数问题。"
            )
            return self._finish_without_query(
                state,
                answer=answer,
                name="refuse_out_of_scope",
                display_name="越界拒答",
                category=category,
            )
        return None

    def _capability_answer(self, state: AgentRuntimeState) -> str:
        record = state.record
        schema = None
        if record.dataset_id:
            schema = self._semantic_schema_provider.build_dataset_schema(
                state.run.oid,
                record.dataset_id,
            )
        return build_capability_answer(schema)

    def _refuse_question(
        self,
        state: AgentRuntimeState,
        refusal: ClarificationRefusal,
    ) -> Iterator[RenderEvent]:
        """理解校验无法澄清时的拒答收口：给出原因与建议问法，正常成功结束。"""

        yield from self._finish_without_query(
            state,
            answer=refusal.answer,
            name="refuse_unclear_question",
            display_name="拒答并给出建议",
            category="data_query",
        )

    def _finish_without_query(
        self,
        state: AgentRuntimeState,
        *,
        answer: str,
        name: str,
        display_name: str,
        category: str,
    ) -> Iterator[RenderEvent]:
        """不产出 SQL/图表的终端回答收口（直答/拒答共用）。"""

        run_id = state.require_run_id()
        with self._trace_recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"phase:{name}",
                node_type=TraceNodeType.PHASE,
                name=name,
                display_name=display_name,
            ),
            input_data={"category": category},
            input_detail={"answer": answer},
        ) as node:
            yield from self._lifecycle.finish(state, answer=answer, chart={}, sql=None)
            node.set_output({"run_status": state.run.status})

    def _suspend_for_preflight_clarification(
        self,
        state: AgentRuntimeState,
        output: ClarificationCard,
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


def _category_of(understanding: dict[str, Any]) -> str:
    """读取分诊类别；旧快照缺失该字段时按 data_query 处理。"""

    category = str(understanding.get("category") or "data_query")
    return category if category in {"chitchat", "data_query", "meta_query", "out_of_scope"} else "data_query"


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
