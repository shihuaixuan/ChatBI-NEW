"""FAST 单查询固定阶段编排。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from apps.chatbi.models import AgentClarificationResumeKind
from apps.chatbi.models.dto.analysis_plan import (
    AnalysisPlan,
    AnalysisPlanStatus,
    CompiledQuery,
    PlanValidation,
    PresentationHint,
    QueryTask,
    QueryTaskSpec,
)
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tool_results import ChatBIToolResultProcessor
from apps.chatbi.orchestration.agent.tools.interaction import (
    prepare_semantic_clarification_args,
)
from apps.chatbi.orchestration.pipeline.events import PipelineEvents
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.generation.agent_finalization import (
    AgentFinalizationInput,
    AgentFinalizationService,
)
from apps.chatbi.services.generation.answer_composer import (
    AnswerComposer,
    AnswerComposerInput,
)
from apps.chatbi.services.generation.fallback_sql import (
    AssistedFallbackSQLService,
    FallbackSQLInput,
)
from apps.chatbi.services.planning.confidence import (
    ConfidenceSignals,
    assess_confidence,
)
from apps.event import EventPublisher, RenderEvent
from apps.tool import ToolCall, ToolCallContext, ToolRegistry, ToolResult, ToolStatus


class FastPipelineError(RuntimeError):
    """FAST 阶段失败，必须由上层按明确错误收口。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True, slots=True)
class FastPipelineDependencies:
    registry: ToolRegistry
    result_processor: ChatBIToolResultProcessor
    finalization_service: AgentFinalizationService
    lifecycle: AgentLifecycle
    event_publisher: EventPublisher
    session: Any
    answer_composer: AnswerComposer | None = None
    assisted_fallback_service: AssistedFallbackSQLService | None = None
    assisted_fallback_enabled: bool = False
    semantic_schema_provider: Any | None = None


class FastPipeline:
    """理解完成后的单查询路径，不进入 AgentReasoner 或 AgentToolExecutor。"""

    def __init__(self, dependencies: FastPipelineDependencies) -> None:
        self._registry = dependencies.registry
        self._result_processor = dependencies.result_processor
        self._finalization_service = dependencies.finalization_service
        self._lifecycle = dependencies.lifecycle
        self._events = PipelineEvents(dependencies.event_publisher)
        self._session = dependencies.session
        self._answer_composer = dependencies.answer_composer
        self._assisted_fallback_service = dependencies.assisted_fallback_service
        self._assisted_fallback_enabled = dependencies.assisted_fallback_enabled
        self._semantic_schema_provider = dependencies.semantic_schema_provider

    def run(self, state: AgentRuntimeState) -> Iterator[RenderEvent]:
        """执行 bind→plan→validate→execute→answer 固定阶段。"""

        run_id = state.require_run_id()
        self._require_understanding(state)
        plan_id = f"fast-{run_id}"

        # bind：复用已确认的问题理解，通过现有语义检索服务产生可信范围。
        yield self._events.plan_created(
            run_id,
            {"record_id": state.record.id, "run_id": run_id, "plan_id": plan_id, "status": "DRAFT"},
        )
        self._session.commit()
        if state.context.semantic_asset_scope is None:
            try:
                self._call_tool(state, "search_semantic_assets", {})
                self._record_confidence(state)
            except FastPipelineError as exc:
                if self._can_use_assisted_fallback(state):
                    yield from self._run_assisted_fallback(state, str(exc))
                    return
                raise
        if self._is_ambiguous(state):
            yield from self._suspend_semantic_clarification(state, plan_id)
            return
        query_task = self._build_query_task(state, plan_id)
        draft_plan = AnalysisPlan(
            id=plan_id,
            tasks=(query_task,),
            presentation=PresentationHint(primary_result=query_task.id),
            validation=PlanValidation(status=AnalysisPlanStatus.DRAFT),
        )
        self._save_plan(state, draft_plan)
        yield self._events.plan_updated(
            run_id,
            {"record_id": state.record.id, "run_id": run_id, "plan_id": plan_id, "status": "DRAFT"},
        )
        self._session.commit()

        yield self._events.task_started(
            run_id,
            {"record_id": state.record.id, "run_id": run_id, "plan_id": plan_id, "task_id": query_task.id, "status": "running"},
        )
        self._session.commit()

        # plan/validate：严格模式由语义计划指纹证明，迁移期模式由编译工具复用既有校验。
        compiled_result = self._call_tool(state, "compile_semantic_sql", {})
        compiled = compiled_result.data
        if compiled is None:
            raise FastPipelineError("FAST_COMPILE_RESULT_MISSING")
        compiled_payload = compiled.model_dump(mode="json")
        completed_task = query_task.model_copy(
            update={
                "compiled": CompiledQuery(
                    plan_fingerprint=self._plan_fingerprint(state),
                    sql=str(compiled_payload["sql"]),
                    tables=tuple(str(item) for item in compiled_payload.get("tables") or []),
                )
            }
        )
        proven_plan = draft_plan.model_copy(
            update={
                "tasks": (completed_task,),
                "validation": PlanValidation(
                    status=AnalysisPlanStatus.PROVEN,
                    reports=(self._validation_report(state),),
                ),
            }
        )
        self._save_plan(state, proven_plan)
        yield self._events.plan_updated(
            run_id,
            {"record_id": state.record.id, "run_id": run_id, "plan_id": plan_id, "status": "PROVEN"},
        )
        self._session.commit()

        self._call_tool(state, "validate_sql", {"sql": str(compiled_payload["sql"])})
        state.context.state["result_node_id"] = query_task.id
        self._call_tool(
            state,
            "execute_sql",
            {"sql": str(compiled_payload["sql"])},
        )
        # 查询结果集引用必须在回答收口前写入 Run 快照，保证 Timeline/恢复可读取。
        self._persist_state(state)
        execution = state.context.state.get("last_execution")
        result_set_id = execution.get("result_set_id") if isinstance(execution, dict) else None
        yield self._events.task_finished(
            run_id,
            {
                "record_id": state.record.id,
                "run_id": run_id,
                "plan_id": plan_id,
                "task_id": query_task.id,
                "result_set_id": result_set_id,
                "status": "succeeded",
            },
        )
        self._session.commit()

        # answer：只把真实结果摘要交给现有回答服务，算术不交给模型。
        rows = state.context.state.get("full_data")
        if not isinstance(rows, list):
            rows = (execution or {}).get("sample_rows") if isinstance(execution, dict) else []
        if not isinstance(rows, list):
            rows = []
        understanding = state.context.state.get("question_understanding")
        intent = understanding.get("intent") if isinstance(understanding, dict) else {}
        question = str(state.context.state.get("question") or state.record.question or "")
        if self._answer_composer is not None:
            final = self._answer_composer.compose(
                AnswerComposerInput(
                    question=question,
                    intent=intent if isinstance(intent, dict) else {},
                    execution=execution if isinstance(execution, dict) else {"status": "succeeded"},
                    rows=rows,
                    plan=state.context.state.get("analysis_plan") if isinstance(state.context.state.get("analysis_plan"), dict) else {},
                    semantic_context=state.context.state.get("semantic_scope") if isinstance(state.context.state.get("semantic_scope"), dict) else {},
                    mode="fast",
                )
            )
        else:
            final = self._finalization_service.generate(
                AgentFinalizationInput(
                    question=question,
                    intent=intent if isinstance(intent, dict) else {},
                    execution=execution if isinstance(execution, dict) else {"status": "succeeded"},
                    rows=rows,
                )
            )
        yield from self._lifecycle.finish(
            state,
            answer=final.answer,
            chart=final.chart,
            sql=str(compiled_payload["sql"]),
            full_data=state.context.state.get("full_data"),
            execution=execution if isinstance(execution, dict) else None,
            claims=list(getattr(final, "claims", []) or []),
            caliber_card=dict(getattr(final, "caliber_card", {}) or {}),
            chart_spec=dict(getattr(final, "chart_spec", {}) or {}),
        )

    def _call_tool(
        self,
        state: AgentRuntimeState,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult[Any]:
        run_id = state.require_run_id()
        tool = self._registry.get(name)
        if tool is None:
            raise FastPipelineError(f"FAST_TOOL_NOT_REGISTERED:{name}")
        call_id = f"fast:{run_id}:{name}"
        result = self._registry.execute(
            ToolCall(name=name, args=args, call_id=call_id),
            state.context,
            call_context=ToolCallContext(tool_call_id=call_id, cancellation=state.cancellation),
        )
        projection = self._result_processor.process(state.context, name, result)
        state.context.state.update(projection.state_patch)
        if projection.result.status != ToolStatus.SUCCEEDED:
            raise FastPipelineError(
                projection.result.error_code or f"FAST_{name.upper()}_FAILED",
                projection.result.model_content,
            )
        return projection.result

    def _can_use_assisted_fallback(self, state: AgentRuntimeState) -> bool:
        """兜底只在全局开关、服务装配和数据集 ASSISTED 策略同时满足时启用。"""

        if not self._assisted_fallback_enabled or self._assisted_fallback_service is None:
            return False
        if self._semantic_schema_provider is None or state.context.dataset_id is None:
            return False
        schema = self._semantic_schema_provider.build_dataset_schema(
            state.context.workspace_id,
            state.context.dataset_id,
        )
        return str((schema.query_config or {}).get("semanticEnforcement") or "LEGACY").upper() == "ASSISTED"

    @staticmethod
    def _record_confidence(state: AgentRuntimeState) -> None:
        """把统一四档判定写入运行态，供口径卡片和审计读取。"""

        package = state.context.state.get("semantic_package")
        scope = state.context.state.get("semantic_scope")
        understanding = state.context.state.get("question_understanding")
        intent = understanding.get("intent", {}) if isinstance(understanding, dict) else {}
        decision = package.get("decision", {}) if isinstance(package, dict) else {}
        confidence = intent.get("confidence", 0.0) if isinstance(intent, dict) else 0.0
        validation_status = "unknown"
        if isinstance(scope, dict):
            validation = scope.get("validation_report") or {}
            validation_status = str(
                validation.get("status")
                or (scope.get("query_plan") or {}).get("validation_status")
                or "unknown"
            )
        assessment = assess_confidence(
            ConfidenceSignals(
                binding_confidence=float(confidence or 0.0),
                evidence_level="exact" if decision.get("status") == "resolved" else "rerank",
                validation_status=validation_status,
                verified_hit=bool(package.get("examples")) if isinstance(package, dict) else False,
                semantic_enforcement=str((scope or {}).get("semantic_enforcement") or "LEGACY") if isinstance(scope, dict) else "LEGACY",
                ambiguous=decision.get("status") == "ambiguous",
            )
        )
        state.context.state["confidence_assessment"] = {
            "route": assessment.route,
            "score": assessment.score,
            "reasons": list(assessment.reasons),
            "certified": assessment.certified,
            "fallback_allowed": assessment.fallback_allowed,
            "evidence": assessment.evidence,
        }

    def _run_assisted_fallback(
        self,
        state: AgentRuntimeState,
        failure_reason: str,
    ) -> Iterator[RenderEvent]:
        """ASSISTED 兜底仍经过物理 Schema、DatasourceQueryService 两次安全闸。"""

        if self._semantic_schema_provider is None or self._assisted_fallback_service is None:
            raise FastPipelineError("FAST_ASSISTED_FALLBACK_NOT_CONFIGURED")
        schema_result = self._call_tool(state, "get_dataset_schema", {})
        physical_schema = (
            schema_result.data.model_dump(mode="json")
            if schema_result.data is not None
            else {"tables": []}
        )
        question = str(state.context.state.get("question") or state.record.question or "")
        result = self._assisted_fallback_service.generate_and_execute(
            FallbackSQLInput(
                question=question,
                datasource_id=state.context.datasource_id or 0,
                user_id=state.context.user_id or 0,
                workspace_id=state.context.workspace_id,
                schema=physical_schema,
                exemplars=[],
                selected_tables=list(state.context.state.get("allowed_tables") or []),
            )
        )
        execution = {
            "status": "succeeded",
            "sql": result.sql,
            "sql_source": result.sql_source,
            "fields": result.fields,
            "row_count": result.row_count,
            "sample_rows": result.sample_rows,
            "stats_summary": result.stats_summary,
            "full_data": result.rows,
        }
        state.context.state["last_execution"] = execution
        state.context.state["full_data"] = result.rows
        understanding = state.context.state.get("question_understanding")
        intent = understanding.get("intent", {}) if isinstance(understanding, dict) else {}
        if self._answer_composer is not None:
            final = self._answer_composer.compose(
                AnswerComposerInput(
                    question=question,
                    intent=intent if isinstance(intent, dict) else {},
                    execution=execution,
                    rows=result.rows,
                    semantic_context={"semantic_enforcement": "ASSISTED", "certified": False},
                    mode="fast",
                )
            )
            answer = final.answer + "\n\n> " + result.warnings[0]
            chart = final.chart
            claims = final.claims
            caliber_card = final.caliber_card
            chart_spec = final.chart_spec
        else:
            answer = (
                "已使用 ASSISTED 兜底完成查询，但未形成认证语义口径；以下为查询结果。"
                f"（原语义路径：{failure_reason}）"
            )
            chart = {"type": "table", "columns": [{"name": field, "value": field} for field in result.fields], "data": result.rows[:100]}
            claims = []
            caliber_card = {"certified": False}
            chart_spec = chart
        yield from self._lifecycle.finish(
            state,
            answer=answer,
            chart=chart,
            sql=result.sql,
            full_data=result.rows,
            execution=execution,
            claims=claims,
            caliber_card=caliber_card,
            chart_spec=chart_spec,
        )

    @staticmethod
    def _require_understanding(state: AgentRuntimeState) -> None:
        if not isinstance(state.context.state.get("question_understanding"), dict):
            raise FastPipelineError("FAST_QUESTION_UNDERSTANDING_REQUIRED")

    def _suspend_semantic_clarification(
        self,
        state: AgentRuntimeState,
        plan_id: str,
    ) -> Iterator[RenderEvent]:
        """绑定歧义沿用现有可验证澄清恢复协议，不把歧义强行编译。"""

        clarification = prepare_semantic_clarification_args(state.context.state)
        if clarification is None or not clarification.options:
            raise FastPipelineError("FAST_SEMANTIC_CLARIFICATION_OPTIONS_MISSING")
        if not state.chatbi_budget.record_clarification().allowed:
            raise FastPipelineError("FAST_CLARIFICATION_BUDGET_EXHAUSTED")
        scope = state.context.state.get("semantic_scope")
        retrieval_id = scope.get("retrieval_id") if isinstance(scope, dict) else None
        options = [item.model_dump(mode="json") for item in clarification.options]
        call_id = f"fast:{state.require_run_id()}:semantic_clarification"
        yield self._lifecycle.suspend(
            state,
            clarification.question,
            options,
            call_id,
            None,
            resume_kind=AgentClarificationResumeKind.AGENT_TOOL,
            resume_payload={
                "operation": "resolve_semantic_bindings",
                "retrieval_id": retrieval_id,
                "options": options,
                "plan_id": plan_id,
            },
        )

    @staticmethod
    def _is_ambiguous(state: AgentRuntimeState) -> bool:
        scope = state.context.semantic_asset_scope
        return bool(scope is not None and scope.decision_status.value == "ambiguous")

    @staticmethod
    def _plan_fingerprint(state: AgentRuntimeState) -> str:
        scope = state.context.semantic_asset_scope
        if scope is not None and scope.query_plan is not None:
            return scope.query_plan.fingerprint
        return "legacy-" + str(state.require_run_id())

    @staticmethod
    def _validation_report(state: AgentRuntimeState) -> dict[str, Any]:
        scope = state.context.semantic_asset_scope
        if scope is None or scope.validation_report is None:
            return {"status": "PROVEN", "source": "legacy_compile"}
        return scope.validation_report.model_dump(mode="json")

    @staticmethod
    def _build_query_task(state: AgentRuntimeState, plan_id: str) -> QueryTask:
        scope = state.context.semantic_asset_scope
        if scope is None:
            raise FastPipelineError("FAST_SEMANTIC_SCOPE_REQUIRED")
        if scope.query_plan is not None:
            plan = scope.query_plan
            spec = QueryTaskSpec(
                dataset_id=plan.dataset_id,
                metric_ids=tuple(item.metric_id for item in plan.metrics),
                dimension_ids=tuple(item.physical_dimension_id for item in plan.dimensions),
                filters=tuple(item.model_dump(mode="json") for item in plan.filters),
                time_range=plan.time_binding.time_range,
                time_dimension_id=plan.time_binding.dimension_id,
                time_grain=plan.time_binding.grain,
                select_mode=str(plan.query_shape.get("select_mode") or "aggregate"),
                query_shape=str(plan.query_shape.get("shape") or plan.query_shape.get("query_shape") or "single_query"),
                order_by=tuple(plan.order_by),
                limit=plan.limit,
            )
        elif scope.compile_plan is not None:
            compile_plan = scope.compile_plan
            temporal = compile_plan.temporal_plan
            time_bucket = temporal.time_bucket
            spec = QueryTaskSpec(
                dataset_id=scope.dataset_id,
                metric_ids=tuple(compile_plan.metric_asset_ids),
                dimension_ids=tuple(compile_plan.dimension_asset_ids),
                filters=tuple(
                    item.model_dump(mode="json")
                    for item in (*compile_plan.filters, *temporal.filters)
                ),
                time_range=scope.normalized_time_range,
                time_dimension_id=time_bucket.dimension_id if time_bucket else None,
                time_grain=time_bucket.grain if time_bucket else None,
                query_shape=str(compile_plan.query_shape.get("shape") or "single_query"),
                order_by=tuple(item.model_dump(mode="json") for item in compile_plan.order_by),
                limit=compile_plan.limit,
            )
        else:
            raise FastPipelineError("FAST_COMPILE_PLAN_REQUIRED")
        return QueryTask(id="q1", spec=spec)

    @staticmethod
    def _save_plan(state: AgentRuntimeState, plan: AnalysisPlan) -> None:
        state.context.state["analysis_plan"] = plan.model_dump(mode="json")
        FastPipeline._persist_state(state)

    @staticmethod
    def _persist_state(state: AgentRuntimeState) -> None:
        agent_run_repository.update_run(
            state.context.session,
            state.run,
            derived_state=state.persistable_context(),
        )
        state.context.session.commit()


__all__ = ["FastPipeline", "FastPipelineDependencies", "FastPipelineError"]
