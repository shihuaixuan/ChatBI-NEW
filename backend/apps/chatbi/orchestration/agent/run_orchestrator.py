"""RunOrchestrator：统一运行路由以及 Fast/Plan 执行管道。"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from typing import Any

from apps.chatbi.errors import QuestionUnderstandingError, SemanticClarificationError
from apps.chatbi.models import (
    AgentClarificationResumeKind,
    AgentErrorClass,
    AgentRunStatus,
    ChatbiAgentClarification,
    ChatbiAgentRun,
)
from apps.chatbi.models.dto.research import ResearchBudget
from apps.chatbi.models.dto.semantic_parse import SemanticParseOutput
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.preparation import AgentInputPreparer
from apps.chatbi.orchestration.agent.state import (
    AgentRuntimeState,
    AgentRuntimeStateFactory,
)
from apps.chatbi.orchestration.pipeline.fast import FastPipeline, FastPipelineError
from apps.chatbi.orchestration.pipeline.mode_router import (
    ModeRouteInput,
    ModeRouter,
    ModeRoutingError,
)
from apps.chatbi.orchestration.pipeline.plan_mode import PlanPipeline, PlanPipelineError
from apps.chatbi.orchestration.pipeline.research import (
    ResearchPipeline,
    ResearchPipelineError,
)
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.event import EventPublisher, RenderEvent
from apps.trace import (
    AgentTraceRecorder,
    TraceNodeHandle,
    TraceNodeSpec,
    TraceNodeStatus,
    TraceNodeType,
    agent_attributes,
)

__all__ = ["RunOrchestrator"]


class RunOrchestrator:
    def __init__(
        self,
        session: Any,
        *,
        event_publisher: EventPublisher,
        recorder: AgentTraceRecorder,
        lifecycle: AgentLifecycle,
        input_preparer: AgentInputPreparer,
        state_factory: AgentRuntimeStateFactory,
        fast_pipeline: FastPipeline | None = None,
        plan_pipeline: PlanPipeline | None = None,
        research_pipeline: ResearchPipeline | None = None,
        mode_router: ModeRouter | None = None,
    ) -> None:
        self.session = session
        self.event_publisher = event_publisher
        self.recorder = recorder
        self.lifecycle = lifecycle
        self.input_preparer = input_preparer
        self.state_factory = state_factory
        self.fast_pipeline = fast_pipeline
        self.plan_pipeline = plan_pipeline
        self.research_pipeline = research_pipeline
        if mode_router is None:
            raise ValueError("AGENT_MODE_ROUTER_REQUIRED")
        self.mode_router = mode_router

    # ---- 入口 ----

    def run(self, run: ChatbiAgentRun, record: Any) -> Iterator[RenderEvent]:
        """在完整生成器生命周期内记录一次 Agent 调用。"""

        terminal_status: TraceNodeStatus | None = None
        with self.recorder.node(
            TraceNodeSpec(
                run_id=run.id or 0,
                node_key="invocation:initial",
                node_type=TraceNodeType.INVOCATION,
                name="invoke_agent",
                display_name="首次执行",
                attributes=agent_attributes(
                    run_id=run.id or 0,
                    record_id=record.id or 0,
                    chat_id=run.chat_id,
                ),
            ),
            input_data={"record_id": record.id or 0, "chat_id": run.chat_id},
        ) as node:
            terminal_status = yield from _trace_terminal_result(
                self._run(run, record),
                node,
            )
        if (
            terminal_status is not None
            and terminal_status is not TraceNodeStatus.WAITING
        ):
            self.recorder.finish_run(
                run.id or 0,
                terminal_status,
                output_summary={"run_status": run.status},
                error_code=run.error_class,
                error_category="agent_run" if run.error else None,
                error=run.error,
            )

    def _run(self, run: ChatbiAgentRun, record: Any) -> Iterator[RenderEvent]:
        state = self.state_factory.create(run, record)
        yield from self.lifecycle.start(state)
        if state.run.status in {
            AgentRunStatus.CANCEL_REQUESTED.value,
            AgentRunStatus.CANCELLED.value,
        }:
            return

        try:
            ready = yield from self.input_preparer.prepare_initial(state)
            if not ready:
                return
            clarification_event = self._semantic_parse_clarification_event(state)
            if clarification_event is not None:
                yield clarification_event
                return
            selected_mode = self._select_mode(state)
            if selected_mode == "fast":
                if self.fast_pipeline is None:
                    yield from self.lifecycle.fail(
                        state,
                        "FAST 编排器未装配。",
                        AgentErrorClass.PLAN_INVALID.value,
                    )
                    return
                agent_run_repository.update_run(
                    self.session,
                    state.run,
                    execution_mode=selected_mode,
                )
                self.session.commit()
                try:
                    yield from self.fast_pipeline.run(state)
                except FastPipelineError as exc:
                    yield from self.lifecycle.fail(
                        state,
                        str(exc),
                        AgentErrorClass.PLAN_INVALID.value,
                        error_details={"code": exc.code},
                    )
                return
            if selected_mode in {"plan", "research"}:
                agent_run_repository.update_run(
                    self.session,
                    state.run,
                    execution_mode=selected_mode,
                )
                self.session.commit()
                if selected_mode == "plan" and self.plan_pipeline is not None:
                    try:
                        yield from self.plan_pipeline.run(state)
                    except PlanPipelineError as exc:
                        yield from self.lifecycle.fail(
                            state,
                            str(exc),
                            AgentErrorClass.PLAN_INVALID.value,
                            error_details={"code": exc.code},
                        )
                elif selected_mode == "research" and self.research_pipeline is not None:
                    try:
                        yield from self.research_pipeline.run(state)
                    except ResearchPipelineError as exc:
                        yield from self.lifecycle.fail(
                            state,
                            str(exc),
                            AgentErrorClass.PLAN_INVALID.value,
                            error_details={"code": exc.code},
                        )
                else:
                    yield from self.lifecycle.fail(
                        state,
                        f"{selected_mode.upper()} 模式尚未实现。",
                        AgentErrorClass.PLAN_INVALID.value,
                        error_details={
                            "code": f"{selected_mode.upper()}_MODE_NOT_READY"
                        },
                    )
                return
            yield from self.lifecycle.fail(
                state,
                f"不支持的执行模式：{selected_mode}",
                AgentErrorClass.PLAN_INVALID.value,
            )
        except ModeRoutingError as exc:
            yield from self.lifecycle.fail(
                state,
                str(exc),
                AgentErrorClass.PLAN_INVALID.value,
                error_details={"code": str(exc)},
            )
        except QuestionUnderstandingError as exc:
            yield from self.lifecycle.fail(
                state,
                str(exc),
                AgentErrorClass.UNDERSTANDING.value,
                error_details=exc.details,
            )
        except Exception as exc:  # 任意未预期异常收敛为失败事件，避免 SSE 静默中断。
            message = str(exc) or exc.__class__.__name__
            yield from self.lifecycle.fail(
                state, message, AgentErrorClass.UNEXPECTED.value
            )

    def resume(
        self,
        run: ChatbiAgentRun,
        record: Any,
        clarification: ChatbiAgentClarification,
        answer_text: str,
    ) -> Iterator[RenderEvent]:
        """以新的调用 span 恢复挂起的 Agent run。"""

        clarification_id = getattr(clarification, "id", None)
        resume_key = clarification_id or clarification.tool_call_id or "pending"
        terminal_status: TraceNodeStatus | None = None
        with self.recorder.node(
            TraceNodeSpec(
                run_id=run.id or 0,
                node_key=f"invocation:resume:{resume_key}",
                node_type=TraceNodeType.INVOCATION,
                name="invoke_agent",
                display_name="澄清恢复",
                attributes=agent_attributes(
                    run_id=run.id or 0,
                    record_id=record.id or 0,
                    chat_id=run.chat_id,
                ),
                metadata={"clarification_id": clarification_id},
            ),
            input_data={"record_id": record.id or 0, "chat_id": run.chat_id},
        ) as node:
            terminal_status = yield from _trace_terminal_result(
                self._resume(run, record, clarification, answer_text),
                node,
            )
        if (
            terminal_status is not None
            and terminal_status is not TraceNodeStatus.WAITING
        ):
            self.recorder.finish_run(
                run.id or 0,
                terminal_status,
                output_summary={"run_status": run.status},
                error_code=run.error_class,
                error_category="agent_run" if run.error else None,
                error=run.error,
            )

    def _select_mode(self, state: AgentRuntimeState) -> str:
        """模式选择。"""

        semantic_parse_payload = state.context.state.get("semantic_parse")
        candidate_groups = state.context.state.get("candidate_groups")
        if not isinstance(semantic_parse_payload, dict):
            raise ModeRoutingError("SEMANTIC_PARSE_STATE_REQUIRED")
        if not isinstance(candidate_groups, dict):
            raise ModeRoutingError("SEMANTIC_CANDIDATES_STATE_REQUIRED")
        try:
            semantic_parse = SemanticParseOutput.model_validate(semantic_parse_payload)
        except ValueError as exc:
            raise ModeRoutingError("SEMANTIC_PARSE_STATE_INVALID") from exc
        research_defaults = ResearchBudget()
        config = state.context.config
        result = self.mode_router.route(
            ModeRouteInput(
                semantic_parse=semantic_parse,
                candidate_groups=candidate_groups,
                dataset_id=state.context.dataset_id or 0,
                tenant_id=state.context.oid,
                enabled_modes=tuple(
                    getattr(state.context.config, "execution_modes", ())
                    or ("fast", "plan")
                ),
                temporal_context=state.temporal_context,
                datasource_id=state.context.datasource_id,
                research_budget=ResearchBudget(
                    max_iterations=getattr(
                        config,
                        "research_max_iterations",
                        research_defaults.max_iterations,
                    ),
                    max_queries=getattr(
                        config,
                        "research_max_queries",
                        research_defaults.max_queries,
                    ),
                    max_model_calls=getattr(
                        config,
                        "research_max_model_calls",
                        research_defaults.max_model_calls,
                    ),
                    max_actions_per_iteration=(
                        getattr(
                            config,
                            "research_max_actions_per_iteration",
                            research_defaults.max_actions_per_iteration,
                        )
                    ),
                    max_duration_seconds=(
                        getattr(
                            config,
                            "research_max_duration_seconds",
                            research_defaults.max_duration_seconds,
                        )
                    ),
                    max_evidence_rows=getattr(
                        config,
                        "research_max_evidence_rows",
                        research_defaults.max_evidence_rows,
                    ),
                    max_evidence_chars=(
                        getattr(
                            config,
                            "research_max_evidence_chars",
                            research_defaults.max_evidence_chars,
                        )
                    ),
                ),
            )
        )
        state.context.state["execution_requirement"] = result
        return str(result["route"]["mode"])

    def _semantic_parse_clarification_event(
        self,
        state: AgentRuntimeState,
    ) -> RenderEvent | None:
        """语义解析未解决时挂起运行，不把业务歧义转换成计划失败。"""

        payload = state.context.state.get("semantic_parse")
        if not isinstance(payload, dict) or payload.get("status") == "resolved":
            return None
        if payload.get("status") != "needs_clarification":
            raise ModeRoutingError("SEMANTIC_PARSE_NOT_RESOLVED")
        if not state.chatbi_budget.record_clarification().allowed:
            raise ModeRoutingError("SEMANTIC_CLARIFICATION_BUDGET_EXHAUSTED")
        unresolved = [
            item for item in payload.get("unresolved") or [] if isinstance(item, dict)
        ]
        candidate_refs = {
            str(ref)
            for item in unresolved
            for ref in item.get("candidate_refs") or []
            if ref
        }
        candidate_groups = state.context.state.get("candidate_groups")
        candidate_groups = (
            candidate_groups if isinstance(candidate_groups, dict) else {}
        )
        candidates = {
            str(item.get("ref")): item
            for group in candidate_groups.values()
            if isinstance(group, list)
            for item in group
            if isinstance(item, dict) and item.get("ref")
        }
        options = [
            {
                "label": str(
                    candidates[ref].get("display_name")
                    or candidates[ref].get("biz_name")
                    or ref
                ),
                "value": ref,
            }
            for ref in sorted(candidate_refs)
            if ref in candidates
        ]
        reasons = [str(item.get("reason") or "").strip() for item in unresolved]
        question = "；".join(item for item in reasons if item)
        if not question:
            question = "请补充需要查询的业务口径。"
        return self.lifecycle.suspend(
            state,
            question,
            options,
            f"semantic-parse:{state.require_run_id()}",
            None,
            resume_kind=AgentClarificationResumeKind.QUESTION_UNDERSTANDING,
            resume_payload={"operation": "resume_semantic_parse"},
        )

    def _resume(
        self,
        run: ChatbiAgentRun,
        record: Any,
        clarification: ChatbiAgentClarification,
        answer_text: str,
    ) -> Iterator[RenderEvent]:
        """从澄清记录声明的恢复边界继续，不重跑已经完成的问题理解。"""

        state = self.state_factory.create(run, record)
        try:
            if state.cancellation.is_cancelled():
                yield from self.lifecycle.cancel(
                    state,
                    "用户在 Agent 恢复前请求取消运行",
                )
                return
            ready = yield from self.input_preparer.prepare_resume(
                state,
                clarification,
                answer_text,
            )
            if not ready:
                return
            clarification_event = self._semantic_parse_clarification_event(state)
            if clarification_event is not None:
                yield clarification_event
                return
            selected_mode = self._select_mode(state)
            if selected_mode == "fast" and self.fast_pipeline is not None:
                agent_run_repository.update_run(
                    self.session,
                    state.run,
                    execution_mode=selected_mode,
                )
                self.session.commit()
                try:
                    yield from self.fast_pipeline.run(state)
                except FastPipelineError as exc:
                    yield from self.lifecycle.fail(
                        state,
                        str(exc),
                        AgentErrorClass.PLAN_INVALID.value,
                        error_details={"code": exc.code},
                    )
                return
            if selected_mode == "plan" and self.plan_pipeline is not None:
                agent_run_repository.update_run(
                    self.session,
                    state.run,
                    execution_mode=selected_mode,
                )
                self.session.commit()
                try:
                    yield from self.plan_pipeline.run(state)
                except PlanPipelineError as exc:
                    yield from self.lifecycle.fail(
                        state,
                        str(exc),
                        AgentErrorClass.PLAN_INVALID.value,
                        error_details={"code": exc.code},
                    )
                return
            yield from self.lifecycle.fail(
                state,
                f"不支持的执行模式：{selected_mode}",
                AgentErrorClass.PLAN_INVALID.value,
            )
        except ModeRoutingError as exc:
            yield from self.lifecycle.fail(
                state,
                str(exc),
                AgentErrorClass.PLAN_INVALID.value,
                error_details={"code": str(exc)},
            )
        except QuestionUnderstandingError as exc:
            yield from self.lifecycle.fail(
                state,
                str(exc),
                AgentErrorClass.UNDERSTANDING.value,
                error_details=exc.details,
            )
        except SemanticClarificationError as exc:
            yield from self.lifecycle.fail(
                state,
                str(exc),
                AgentErrorClass.RETRIEVAL.value,
            )
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            yield from self.lifecycle.fail(
                state, message, AgentErrorClass.UNEXPECTED.value
            )


# ---- Trace 结果 ----


def _trace_terminal_result(
    events: Iterator[RenderEvent],
    node: TraceNodeHandle,
) -> Generator[
    RenderEvent,
    None,
    TraceNodeStatus | None,
]:
    """观察业务终止事件更新 Trace，但不拦截或延迟 SSE 事件。"""

    terminal_status: TraceNodeStatus | None = None
    for event in events:
        if event.domain == "run.failed":
            node.set_attribute("gen_ai.agent.result", "failed")
            node.set_status(TraceNodeStatus.FAILED)
            terminal_status = TraceNodeStatus.FAILED
        elif event.domain == "run.finished":
            node.set_attribute("gen_ai.agent.result", "finished")
            node.set_status(TraceNodeStatus.SUCCEEDED)
            terminal_status = TraceNodeStatus.SUCCEEDED
        elif event.domain == "run.cancelled":
            node.set_attribute("gen_ai.agent.result", "cancelled")
            node.set_status(TraceNodeStatus.CANCELLED)
            terminal_status = TraceNodeStatus.CANCELLED
        elif event.domain == "clarification.required":
            node.set_attribute("gen_ai.agent.result", "waiting")
            node.set_status(TraceNodeStatus.WAITING)
            terminal_status = TraceNodeStatus.WAITING
        yield event
    return terminal_status
