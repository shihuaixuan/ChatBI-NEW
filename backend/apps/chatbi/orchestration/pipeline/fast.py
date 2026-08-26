"""FAST 单查询固定阶段编排。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from apps.chatbi.models import AgentClarificationResumeKind
from apps.chatbi.models.dto.analysis_evidence import AnalysisEvidenceLevel
from apps.chatbi.models.dto.analysis_plan import (
    AnalysisPlan,
    AnalysisPlanStatus,
    CompiledQuery,
    PlanValidation,
    PresentationHint,
    QueryTask,
    QueryTaskSpec,
    ResultSetKind,
)
from apps.chatbi.models.dto.execution_requirement import (
    ExecutionRequirement,
    QueryRequirement,
    execution_requirement_from_state,
    query_requirement_to_spec,
)
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tool_results import ChatBIToolResultProcessor
from apps.chatbi.orchestration.agent.tools.interaction import (
    prepare_semantic_clarification_args,
)
from apps.chatbi.orchestration.pipeline.events import PipelineEvents
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.evidence import (
    EvidenceRegistry,
    build_analysis_evidence,
    build_analysis_version_snapshot,
)
from apps.chatbi.services.execution.result_store import ResultStore
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
from apps.chatbi.services.planning.execution_state import (
    PLAN_EXECUTION_STATE_KEY,
    PlanNodeExecutionStatus,
    build_analysis_plan_execution_state,
    load_plan_execution_state,
    transition_plan_node,
)
from apps.chatbi.services.planning.semantic_query_preparation import (
    prepare_strict_query_scope,
)
from apps.conversation import ChatRecordExecutionType
from apps.event import EventPublisher, RenderEvent
from apps.tool import ToolCall, ToolCallContext, ToolRegistry, ToolResult, ToolStatus
from apps.trace import (
    AgentTraceRecorder,
    TraceNodeSpec,
    TraceNodeStatus,
    TraceNodeType,
)
from common.observability import MetricsRecorder


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
    metrics: MetricsRecorder | None = None
    # 直接流水线也必须复用 Agent Trace，不能只在 ReAct 工具执行器中留痕。
    trace_recorder: AgentTraceRecorder | None = None


class FastPipeline:
    """理解完成后的单查询路径，不进入旧 ReAct 执行链。"""

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
        self._metrics = dependencies.metrics
        self._trace_recorder = dependencies.trace_recorder
        self._plan_snapshot_counts: dict[int, int] = {}

    def run(self, state: AgentRuntimeState) -> Iterator[RenderEvent]:
        """执行 bind→plan→validate→execute→answer 固定阶段。"""

        run_id = state.require_run_id()
        if self._metrics is not None:
            self._metrics.record_run(mode="fast", status="started")
        execution_requirement = self._load_execution_requirement(state)
        try:
            execution_requirement.require_ready()
        except ValueError as exc:
            raise FastPipelineError(str(exc)) from exc
        if len(execution_requirement.query_requirements) != 1:
            raise FastPipelineError("FAST_SINGLE_QUERY_REQUIRED")
        if execution_requirement.post_calculations:
            raise FastPipelineError("FAST_POST_CALCULATION_UNEXPECTED")
        plan_id = f"fast-{run_id}"

        # 只有语义绑定完成且可以进入执行计划时，才发布 plan-created 事件。
        # 澄清分支不再留下一个实际上不存在的 DRAFT AnalysisPlan。
        yield self._events.plan_created(
            run_id,
            {
                "record_id": state.record.id,
                "run_id": run_id,
                "plan_id": plan_id,
                "status": "DRAFT",
            },
        )
        self._session.commit()
        query_requirement = execution_requirement.query_requirements[0]
        query_task = self._build_query_task_from_requirement(
            state,
            query_requirement,
        )
        self._prepare_strict_scope_from_requirement(state, query_requirement)
        draft_plan = AnalysisPlan(
            id=plan_id,
            tasks=(query_task,),
            presentation=PresentationHint(primary_result=query_task.id),
            validation=PlanValidation(status=AnalysisPlanStatus.DRAFT),
        )
        self._ensure_plan_execution_state(state, draft_plan)
        self._save_plan(state, draft_plan)
        yield self._events.plan_updated(
            run_id,
            {
                "record_id": state.record.id,
                "run_id": run_id,
                "plan_id": plan_id,
                "status": "DRAFT",
            },
        )
        self._session.commit()

        self._transition_plan_node(
            state, query_task.id, PlanNodeExecutionStatus.RUNNING
        )
        yield self._events.task_started(
            run_id,
            {
                "record_id": state.record.id,
                "run_id": run_id,
                "plan_id": plan_id,
                "task_id": query_task.id,
                "status": "running",
            },
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
                    tables=tuple(
                        str(item) for item in compiled_payload.get("tables") or []
                    ),
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
            {
                "record_id": state.record.id,
                "run_id": run_id,
                "plan_id": plan_id,
                "status": "PROVEN",
            },
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
        result_set_id = (
            execution.get("result_set_id") if isinstance(execution, dict) else None
        )
        self._transition_plan_node(
            state,
            query_task.id,
            PlanNodeExecutionStatus.SUCCEEDED,
        )
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
            rows = (
                (execution or {}).get("sample_rows")
                if isinstance(execution, dict)
                else []
            )
        if not isinstance(rows, list):
            rows = []
        understanding = state.context.state.get("question_understanding")
        intent = understanding.get("intent") if isinstance(understanding, dict) else {}
        question = str(
            state.context.state.get("question") or state.record.question or ""
        )
        if self._answer_composer is not None:
            plan_payload = state.context.state.get("analysis_plan")
            semantic_payload = state.context.state.get("semantic_scope")
            composed = self._answer_composer.compose(
                AnswerComposerInput(
                    question=question,
                    intent=intent if isinstance(intent, dict) else {},
                    execution=execution
                    if isinstance(execution, dict)
                    else {"status": "succeeded"},
                    rows=rows,
                    plan=dict(plan_payload) if isinstance(plan_payload, dict) else {},
                    semantic_context=(
                        dict(semantic_payload)
                        if isinstance(semantic_payload, dict)
                        else {}
                    ),
                    mode="fast",
                )
            )
            answer = composed.answer
            chart = composed.chart
            claims = list(getattr(composed, "claims", []) or [])
            caliber_card = dict(getattr(composed, "caliber_card", {}) or {})
            chart_spec = dict(getattr(composed, "chart_spec", {}) or {})
        else:
            generated = self._finalization_service.generate(
                AgentFinalizationInput(
                    question=question,
                    intent=intent if isinstance(intent, dict) else {},
                    execution=execution
                    if isinstance(execution, dict)
                    else {"status": "succeeded"},
                    rows=rows,
                )
            )
            answer = generated.answer
            chart = generated.chart
            claims = list(getattr(generated, "claims", []) or [])
            caliber_card = dict(getattr(generated, "caliber_card", {}) or {})
            chart_spec = dict(getattr(generated, "chart_spec", {}) or {})
        yield from self._lifecycle.finish(
            state,
            answer=answer,
            chart=chart,
            sql=str(compiled_payload["sql"]),
            full_data=state.context.state.get("full_data"),
            execution=execution if isinstance(execution, dict) else None,
            claims=claims,
            caliber_card=caliber_card,
            chart_spec=chart_spec,
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
        trace_spec = TraceNodeSpec(
            run_id=run_id,
            node_key=(
                f"pipeline:fast:{run_id}:{name}:"
                f"{state.context.state.get('result_node_id') or 'plan'}"
            ),
            node_type=_pipeline_trace_node_type(name),
            name=_pipeline_trace_name(name),
            display_name=_pipeline_trace_display_name(name),
            metadata={"pipeline": "fast", "tool_name": name},
        )
        trace_context = (
            self._trace_recorder.node(
                trace_spec,
                input_data={"tool_name": name, "call_id": call_id},
                input_detail={
                    "args": args,
                    "semantic_retrieval_request": (
                        state.context.state.get("semantic_retrieval_request")
                        if name == "search_semantic_assets"
                        else None
                    ),
                },
            )
            if self._trace_recorder is not None
            else nullcontext(None)
        )
        with trace_context as trace_node:
            result = self._registry.execute(
                ToolCall(name=name, args=args, call_id=call_id),
                state.context,
                call_context=ToolCallContext(
                    tool_call_id=call_id,
                    cancellation=state.cancellation,
                ),
            )
            projection = self._result_processor.process(state.context, name, result)
            state.context.state.update(projection.state_patch)
            if trace_node is not None:
                trace_summary = {
                    "status": projection.result.status.value,
                    "error_code": projection.result.error_code,
                    "changed_keys": sorted(projection.state_patch),
                }
                if name == "search_semantic_assets":
                    # R1 门禁只需要稳定的分槽摘要；完整语义包仍按原规则进入 detail，
                    # 避免大结果截断时丢失“是否整句检索”的证据。
                    metadata = projection.result.metadata
                    if isinstance(metadata, dict):
                        if isinstance(metadata.get("semantic_retrieval_filters"), dict):
                            trace_summary["retrieval_filters"] = metadata[
                                "semantic_retrieval_filters"
                            ]
                        if isinstance(metadata.get("semantic_retrieval_request"), dict):
                            trace_summary["retrieval_request"] = metadata[
                                "semantic_retrieval_request"
                            ]
                trace_node.set_output(trace_summary)
                trace_node.set_output_detail(
                    {
                        "result": _pipeline_trace_result(projection.result),
                        "state_patch": projection.state_patch,
                    }
                )
                if projection.result.status != ToolStatus.SUCCEEDED:
                    trace_node.set_status(TraceNodeStatus.FAILED)
            if projection.result.status != ToolStatus.SUCCEEDED:
                raise FastPipelineError(
                    projection.result.error_code or f"FAST_{name.upper()}_FAILED",
                    projection.result.model_content,
                )
            return projection.result

    def _record_confidence(self, state: AgentRuntimeState) -> None:
        """把统一四档判定写入运行态，供口径卡片和审计读取。"""

        package = state.context.state.get("semantic_package")
        scope = state.context.state.get("semantic_scope")
        understanding = state.context.state.get("question_understanding")
        intent = (
            understanding.get("intent", {}) if isinstance(understanding, dict) else {}
        )
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
                evidence_level="exact"
                if decision.get("status") == "resolved"
                else "rerank",
                validation_status=validation_status,
                verified_hit=bool(package.get("examples"))
                if isinstance(package, dict)
                else False,
                semantic_enforcement=str(
                    (scope or {}).get("semantic_enforcement") or "LEGACY"
                )
                if isinstance(scope, dict)
                else "LEGACY",
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
        if self._metrics is not None:
            self._metrics.record_outcome(assessment.route, mode="fast")

    def _run_assisted_fallback(
        self,
        state: AgentRuntimeState,
        failure_reason: str,
    ) -> Iterator[RenderEvent]:
        """ASSISTED 兜底仍经过物理 Schema、DatasourceQueryService 两次安全闸。"""

        if (
            self._semantic_schema_provider is None
            or self._assisted_fallback_service is None
        ):
            raise FastPipelineError("FAST_ASSISTED_FALLBACK_NOT_CONFIGURED")
        schema_result = self._call_tool(state, "get_dataset_schema", {})
        physical_schema = (
            schema_result.data.model_dump(mode="json")
            if schema_result.data is not None
            else {"tables": []}
        )
        question = str(
            state.context.state.get("question") or state.record.question or ""
        )
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
        if self._metrics is not None:
            self._metrics.record_outcome("assisted_fallback", mode="fast")
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
        self._register_assisted_fallback_evidence(state, execution)
        understanding = state.context.state.get("question_understanding")
        intent = (
            understanding.get("intent", {}) if isinstance(understanding, dict) else {}
        )
        if self._answer_composer is not None:
            final = self._answer_composer.compose(
                AnswerComposerInput(
                    question=question,
                    intent=intent if isinstance(intent, dict) else {},
                    execution=execution,
                    rows=result.rows,
                    semantic_context={
                        "semantic_enforcement": "ASSISTED",
                        "certified": False,
                    },
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
            chart = {
                "type": "table",
                "columns": [{"name": field, "value": field} for field in result.fields],
                "data": result.rows[:100],
            }
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

    def _register_assisted_fallback_evidence(
        self,
        state: AgentRuntimeState,
        execution: dict[str, Any],
    ) -> None:
        result_store = state.context.result_store
        if result_store is None:
            raise FastPipelineError("FAST_RESULT_STORE_REQUIRED")
        plan_id = f"fast-{state.require_run_id()}"
        result_ref = result_store.register(
            execution_id=self._required_execution_id(state),
            execution_type=ChatRecordExecutionType.AGENT,
            chat_id=self._required_chat_id(state),
            record_id=self._required_record_id(state),
            plan_id=plan_id,
            node_id=ResultStore.LEGACY_QUERY_ID,
            kind=ResultSetKind.QUERY,
            fields=[str(item) for item in execution.get("fields") or []],
            rows=[
                item
                for item in execution.get("full_data") or []
                if isinstance(item, dict)
            ],
            row_count=int(execution.get("row_count") or 0),
            source_sql=str(execution.get("sql") or "") or None,
            idempotency_key=f"fast-assisted:{self._required_execution_id(state)}",
        )
        execution["result_set_id"] = result_ref.result_set_id
        execution["artifact_ref"] = result_ref.artifact_ref.model_dump(mode="json")
        evidence = build_analysis_evidence(
            run_id=self._required_execution_id(state),
            mode="fast",
            plan_id=plan_id,
            node_id=ResultStore.LEGACY_QUERY_ID,
            tool_call_id="assisted_fallback",
            result_set_id=result_ref.result_set_id,
            fields=[str(item) for item in execution.get("fields") or []],
            rows=[
                item
                for item in execution.get("full_data") or []
                if isinstance(item, dict)
            ],
            row_count=int(execution.get("row_count") or 0),
            purpose="Fast ASSISTED 兜底结果",
            evidence_level=AnalysisEvidenceLevel.EXPLORATORY,
            limitations=("assisted_semantic_binding",),
            version_snapshot=self._evidence_version_snapshot(state),
        )
        EvidenceRegistry(state.context.state).register(evidence)
        self._persist_state(state)

    @staticmethod
    def _evidence_version_snapshot(state: AgentRuntimeState) -> Any:
        execution = state.context.state.get("execution_requirement")
        asset_snapshot = (
            execution.get("asset_snapshot") if isinstance(execution, dict) else {}
        )
        semantic_scope = state.context.state.get("semantic_scope")
        return build_analysis_version_snapshot(
            asset_snapshot=asset_snapshot if isinstance(asset_snapshot, dict) else {},
            semantic_scope=(semantic_scope if isinstance(semantic_scope, dict) else {}),
        )

    @staticmethod
    def _required_execution_id(state: AgentRuntimeState) -> str:
        value = state.context.execution_id
        if not isinstance(value, str) or not value:
            raise FastPipelineError("FAST_EXECUTION_ID_REQUIRED")
        return value

    @staticmethod
    def _required_chat_id(state: AgentRuntimeState) -> int:
        value = state.context.chat_id
        if not isinstance(value, int) or isinstance(value, bool):
            raise FastPipelineError("FAST_RESULT_OWNERSHIP_REQUIRED")
        return value

    @staticmethod
    def _required_record_id(state: AgentRuntimeState) -> int:
        value = state.context.record_id
        if not isinstance(value, int) or isinstance(value, bool):
            raise FastPipelineError("FAST_RESULT_OWNERSHIP_REQUIRED")
        return value

    @staticmethod
    def _require_understanding(state: AgentRuntimeState) -> None:
        if not isinstance(state.context.state.get("question_understanding"), dict):
            raise FastPipelineError("FAST_QUESTION_UNDERSTANDING_REQUIRED")

    def _prepare_strict_scope_from_requirement(
        self,
        state: AgentRuntimeState,
        requirement: QueryRequirement,
    ) -> None:
        """把新执行需求转换为严格编译器所需的已验证查询计划。"""

        scope = state.context.semantic_asset_scope
        if scope is None or scope.semantic_enforcement != "STRICT":
            return

        execution_payload = state.context.state.get("execution_requirement")
        runtime_value = (
            execution_payload.get("runtime")
            if isinstance(execution_payload, dict)
            else None
        )
        runtime = runtime_value if isinstance(runtime_value, dict) else {}
        dataset_id = runtime.get("dataset_id")
        if not isinstance(dataset_id, int) or isinstance(dataset_id, bool):
            dataset_id = state.context.dataset_id
        if not isinstance(dataset_id, int) or dataset_id <= 0:
            raise FastPipelineError("FAST_EXECUTION_REQUIREMENT_DATASET_REQUIRED")

        try:
            updated_scope = prepare_strict_query_scope(
                scope,
                (requirement,),
                schema_provider=self._semantic_schema_provider,
                workspace_id=state.context.workspace_id,
                dataset_id=dataset_id,
            )
        except ValueError as exc:
            raise FastPipelineError(str(exc)) from exc
        state.context.state["semantic_scope"] = updated_scope.model_dump(mode="json")

    @staticmethod
    def _load_execution_requirement(state: AgentRuntimeState) -> ExecutionRequirement:
        """读取路由阶段产物；执行阶段不再自行补检索或重绑资产。"""

        try:
            return execution_requirement_from_state(state.context.state)
        except ValueError as exc:
            raise FastPipelineError(str(exc)) from exc

    @staticmethod
    def _build_query_task_from_requirement(
        state: AgentRuntimeState,
        requirement: Any,
    ) -> QueryTask:
        """把唯一执行需求转换为 Fast 的唯一查询节点。"""

        runtime = state.context.state.get("execution_requirement")
        runtime = runtime.get("runtime") if isinstance(runtime, dict) else {}
        dataset_id = runtime.get("dataset_id") if isinstance(runtime, dict) else None
        if not isinstance(dataset_id, int) or isinstance(dataset_id, bool):
            dataset_id = state.context.dataset_id
        try:
            spec = query_requirement_to_spec(requirement, dataset_id=dataset_id or 0)
        except ValueError as exc:
            raise FastPipelineError(str(exc)) from exc
        return QueryTask(
            id=f"q:{requirement.id}",
            source_requirement_id=requirement.id,
            spec=spec,
        )

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
        return bool(
            scope is not None
            and scope.decision_status is not None
            and scope.decision_status.value == "ambiguous"
        )

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
                dimension_ids=tuple(
                    item.physical_dimension_id for item in plan.dimensions
                ),
                filters=tuple(item.model_dump(mode="json") for item in plan.filters),
                time_range=plan.time_binding.time_range,
                time_dimension_id=plan.time_binding.dimension_id,
                time_grain=plan.time_binding.grain,
                select_mode=str(plan.query_shape.get("select_mode") or "aggregate"),
                query_shape=str(
                    plan.query_shape.get("shape")
                    or plan.query_shape.get("query_shape")
                    or "single_query"
                ),
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
                query_shape=str(
                    compile_plan.query_shape.get("shape") or "single_query"
                ),
                order_by=tuple(
                    item.model_dump(mode="json") for item in compile_plan.order_by
                ),
                limit=compile_plan.limit,
            )
        else:
            raise FastPipelineError("FAST_COMPILE_PLAN_REQUIRED")
        return QueryTask(id="q1", spec=spec)

    def _save_plan(self, state: AgentRuntimeState, plan: AnalysisPlan) -> None:
        state.context.state["analysis_plan"] = plan.model_dump(mode="json")
        self._ensure_plan_execution_state(state, plan)
        FastPipeline._persist_state(state)
        if self._trace_recorder is None:
            return
        run_id = state.require_run_id()
        snapshot_index = self._plan_snapshot_counts.get(run_id, 0) + 1
        self._plan_snapshot_counts[run_id] = snapshot_index
        with self._trace_recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_key=f"pipeline:fast:plan:{plan.id}:{snapshot_index}",
                node_type=TraceNodeType.PHASE,
                name="analysis_plan_snapshot",
                display_name="记录分析计划快照",
                metadata={"pipeline": "fast", "stage": "plan"},
            ),
            input_data={"plan_id": plan.id, "status": plan.validation.status.value},
        ) as plan_node:
            plan_node.set_output(
                {
                    "plan_id": plan.id,
                    "status": plan.validation.status.value,
                    "query_task_count": sum(
                        isinstance(task, QueryTask) for task in plan.tasks
                    ),
                    "compute_task_count": sum(
                        task.__class__.__name__ == "ComputeTask" for task in plan.tasks
                    ),
                }
            )
            plan_node.set_output_detail({"analysis_plan": plan.model_dump(mode="json")})

    @staticmethod
    def _ensure_plan_execution_state(
        state: AgentRuntimeState,
        plan: AnalysisPlan,
    ) -> None:
        if isinstance(state.context.state.get(PLAN_EXECUTION_STATE_KEY), dict):
            return
        state.context.state[PLAN_EXECUTION_STATE_KEY] = (
            build_analysis_plan_execution_state(plan).model_dump(mode="json")
        )

    def _transition_plan_node(
        self,
        state: AgentRuntimeState,
        node_id: str,
        status: PlanNodeExecutionStatus,
    ) -> None:
        raw = state.context.state.get(PLAN_EXECUTION_STATE_KEY)
        if not isinstance(raw, dict):
            raise FastPipelineError("FAST_PLAN_EXECUTION_STATE_REQUIRED")
        execution_state = load_plan_execution_state(raw)
        if execution_state is None:
            raise FastPipelineError("FAST_PLAN_EXECUTION_STATE_REQUIRED")
        state.context.state[PLAN_EXECUTION_STATE_KEY] = transition_plan_node(
            execution_state, node_id, status
        ).model_dump(mode="json")
        self._persist_state(state)

    @staticmethod
    def _persist_state(state: AgentRuntimeState) -> None:
        agent_run_repository.update_run(
            state.context.session,
            state.run,
            derived_state=state.persistable_context(),
        )
        state.context.session.commit()


__all__ = ["FastPipeline", "FastPipelineDependencies", "FastPipelineError"]


def _pipeline_trace_node_type(name: str) -> TraceNodeType:
    if name == "validate_sql":
        return TraceNodeType.VALIDATION
    if name == "execute_sql":
        return TraceNodeType.TOOL
    return TraceNodeType.PHASE


def _pipeline_trace_name(name: str) -> str:
    return {
        "search_semantic_assets": "semantic_retrieval",
        "compile_semantic_sql": "semantic_compilation",
        "validate_sql": "sql_validation",
        "execute_sql": "sql_execution",
        "get_dataset_schema": "schema_snapshot",
    }.get(name, f"pipeline_{name}")


def _pipeline_trace_display_name(name: str) -> str:
    return {
        "search_semantic_assets": "语义检索与绑定快照",
        "compile_semantic_sql": "语义编译快照",
        "validate_sql": "SQL 校验快照",
        "execute_sql": "SQL 执行快照",
        "get_dataset_schema": "数据集 Schema 快照",
    }.get(name, f"流水线工具：{name}")


def _pipeline_trace_result(result: ToolResult[Any]) -> dict[str, Any]:
    data = result.data
    return {
        "status": result.status.value,
        "model_content": result.model_content,
        "data": data.model_dump(mode="json") if data is not None else None,
        "metadata": result.metadata,
        "error_code": result.error_code,
        "error_category": (
            result.error_category.value if result.error_category is not None else None
        ),
        "details": result.details,
    }
