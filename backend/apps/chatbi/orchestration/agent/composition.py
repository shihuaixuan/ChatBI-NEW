"""ChatBI Agent 运行时组装入口。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from apps.chatbi.adapters.prompts.limited_multistep import (
    DefaultLimitedMultiStepPromptBuilder,
)
from apps.chatbi.adapters.question_model import build_question_model_service
from apps.chatbi.composition import (
    build_agent_event_publisher,
    build_agent_trace_recorder,
    build_chat_record_service,
    build_physical_schema_service,
    build_query_service,
    build_result_artifact_service,
    build_semantic_parse_service,
)
from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.preparation import AgentInputPreparer
from apps.chatbi.orchestration.agent.run_orchestrator import RunOrchestrator
from apps.chatbi.orchestration.agent.state import AgentRuntimeStateFactory
from apps.chatbi.orchestration.agent.tool_results import ChatBIToolResultProcessor
from apps.chatbi.orchestration.agent.tools.base import AgentToolContextServices
from apps.chatbi.orchestration.agent.tools.core import FinishTool
from apps.chatbi.orchestration.agent.tools.interaction import ClarifyTool
from apps.chatbi.orchestration.agent.tools.temporal import ParseTimeRangeTool
from apps.chatbi.orchestration.pipeline.fast import (
    FastPipeline,
    FastPipelineDependencies,
)
from apps.chatbi.orchestration.pipeline.mode_router import ModeRouter
from apps.chatbi.orchestration.pipeline.plan_mode import (
    PlanPipeline,
    PlanPipelineDependencies,
)
from apps.chatbi.services.computation import ComputeEngine
from apps.chatbi.services.execution import (
    QueryTaskExecutor,
    ResultArtifactService,
    ResultStore,
)
from apps.chatbi.services.generation.agent_finalization import AgentFinalizationService
from apps.chatbi.services.generation.answer_composer import AnswerComposer
from apps.chatbi.services.generation.fallback_sql import AssistedFallbackSQLService
from apps.chatbi.services.planning import (
    LimitedMultiStepDecomposer,
    PhysicalSchemaService,
)
from apps.chatbi.services.understanding import SemanticParseService
from apps.datasource.services import DatasourceQueryService
from apps.event import EventPublisher
from apps.knowledge.composition import build_sql_example_query_service
from apps.knowledge.services.sql_example_query_service import SQLExampleQueryService
from apps.memory import MemoryService, build_memory_service
from apps.retrieval import RetrievalService
from apps.retrieval.query.service import build_retrieval_service
from apps.semantic import SemanticSQLCompilationService
from apps.semantic.composition import (
    build_semantic_schema_service,
    build_semantic_sql_compilation_service,
    build_semantic_term_query_service,
)
from apps.semantic.services.schema_service import DatasetSchemaProvider
from apps.semantic.services.term_query_service import SemanticTermQueryService
from apps.tool import ToolRegistry, default_middlewares
from apps.tool.context import CancellationSignal
from apps.tool.tools.datasource import (
    ExecuteSqlTool,
    GetDatasetSchemaTool,
    ValidateSqlTool,
)
from apps.tool.tools.knowledge import GetSqlExamplesTool
from apps.tool.tools.semantic import (
    CompileSemanticSqlTool,
    SearchSemanticAssetsTool,
    SearchTerminologyTool,
)
from apps.trace import AgentTraceRecorder
from common.observability import build_metrics_recorder


def build_agent_tool_registry(
    *,
    query_service: DatasourceQueryService,
    semantic_query_service: SemanticSQLCompilationService,
    semantic_retrieval_service: RetrievalService,
    physical_schema_service: PhysicalSchemaService,
    term_query_service: SemanticTermQueryService,
    sql_example_query_service: SQLExampleQueryService,
    semantic_schema_provider: DatasetSchemaProvider | None = None,
    finalization_service: AgentFinalizationService | None = None,
    answer_composer: AnswerComposer | None = None,
) -> ToolRegistry:
    """装配 Agent 默认工具集合及执行中间件。"""

    registry = ToolRegistry(middlewares=default_middlewares())
    registry.register(
        SearchSemanticAssetsTool(
            semantic_retrieval_service,
            query_service,
            semantic_schema_provider,
        )
    )
    registry.register(CompileSemanticSqlTool(semantic_query_service, query_service))
    registry.register(FinishTool(finalization_service, answer_composer))
    registry.register(ClarifyTool())
    registry.register(ParseTimeRangeTool())
    registry.register(GetDatasetSchemaTool(physical_schema_service))
    registry.register(ValidateSqlTool(query_service))
    registry.register(ExecuteSqlTool(query_service))
    registry.register(SearchTerminologyTool(term_query_service))
    registry.register(GetSqlExamplesTool(sql_example_query_service))
    return registry


def build_run_orchestrator(
    session: Any,
    current_user: Any,
    config: AgentConfig | None = None,
    *,
    registry: ToolRegistry | None = None,
    semantic_parse_service: SemanticParseService | None = None,
    term_query_service: SemanticTermQueryService | None = None,
    query_service: DatasourceQueryService | None = None,
    semantic_query_service: SemanticSQLCompilationService | None = None,
    semantic_retrieval_service: RetrievalService | None = None,
    semantic_schema_provider: DatasetSchemaProvider | None = None,
    physical_schema_service: PhysicalSchemaService | None = None,
    sql_example_query_service: SQLExampleQueryService | None = None,
    result_artifact_service: ResultArtifactService | None = None,
    event_publisher: EventPublisher | None = None,
    recorder: AgentTraceRecorder | None = None,
    input_preparer: AgentInputPreparer | None = None,
    cancellation_signal_factory: Callable[[int], CancellationSignal] | None = None,
    query_task_executor: QueryTaskExecutor | None = None,
    finalization_service: AgentFinalizationService | None = None,
    answer_composer: AnswerComposer | None = None,
    assisted_fallback_service: AssistedFallbackSQLService | None = None,
    memory_service: MemoryService | None = None,
    limited_multistep_decomposer: LimitedMultiStepDecomposer | None = None,
) -> RunOrchestrator:
    """构造依赖完整的 RunOrchestrator；生产入口和测试统一使用此函数。"""

    resolved_config = config or AgentConfig()
    resolved_publisher = event_publisher or build_agent_event_publisher(session)
    resolved_recorder = recorder or build_agent_trace_recorder()
    resolved_memory_service = memory_service or build_memory_service(session)
    lifecycle = AgentLifecycle(
        session,
        current_user.id,
        build_chat_record_service(session),
        resolved_publisher,
        resolved_recorder,
        resolved_memory_service,
    )
    resolved_query_service = query_service or build_query_service(
        session,
        default_limit=resolved_config.default_limit,
        sample_rows=resolved_config.sample_rows,
        max_transient_retries=resolved_config.query_transient_retries,
    )
    resolved_semantic_query_service = (
        semantic_query_service or build_semantic_sql_compilation_service(session)
    )
    resolved_semantic_retrieval_service = (
        semantic_retrieval_service or build_retrieval_service(session)
    )
    resolved_semantic_schema_provider = (
        semantic_schema_provider or build_semantic_schema_service(session)
    )
    resolved_physical_schema_service = (
        physical_schema_service or build_physical_schema_service(session)
    )
    resolved_term_query_service = (
        term_query_service or build_semantic_term_query_service(session)
    )
    resolved_sql_example_query_service = (
        sql_example_query_service or build_sql_example_query_service(session)
    )
    # 重写、语义解析、回答和计划阶段共享同一个结构化模型服务。
    model_service = build_question_model_service(enforce_json=True)
    resolved_limited_multistep_decomposer = (
        limited_multistep_decomposer
        or LimitedMultiStepDecomposer(
            model_service,
            DefaultLimitedMultiStepPromptBuilder(),
        )
    )
    if finalization_service is None:
        resolved_finalization_service = AgentFinalizationService(model_service)
        resolved_answer_composer: AnswerComposer | None = answer_composer or AnswerComposer(
            model_service,
            citation_enforced=resolved_config.answer_citation_enforced,
        )
    else:
        resolved_finalization_service = finalization_service
        resolved_answer_composer = answer_composer
    resolved_assisted_fallback = assisted_fallback_service
    if (
        resolved_assisted_fallback is None
        and resolved_config.assisted_fallback_enabled
        and finalization_service is None
    ):
        resolved_assisted_fallback = AssistedFallbackSQLService(
            model_service, resolved_query_service
        )
    resolved_registry = registry or build_agent_tool_registry(
        query_service=resolved_query_service,
        semantic_query_service=resolved_semantic_query_service,
        semantic_retrieval_service=resolved_semantic_retrieval_service,
        physical_schema_service=resolved_physical_schema_service,
        term_query_service=resolved_term_query_service,
        sql_example_query_service=resolved_sql_example_query_service,
        semantic_schema_provider=resolved_semantic_schema_provider,
        finalization_service=resolved_finalization_service,
        answer_composer=resolved_answer_composer,
    )
    resolved_semantic_parse_service = (
        semantic_parse_service or build_semantic_parse_service(model_service)
    )
    result_processor = ChatBIToolResultProcessor()
    if input_preparer is not None:
        resolved_input_preparer = input_preparer
    else:
        search_tool = resolved_registry.get("search_semantic_assets")
        if not isinstance(search_tool, SearchSemanticAssetsTool):
            raise ValueError("AGENT_INPUT_SEARCH_TOOL_REQUIRED")
        resolved_input_preparer = AgentInputPreparer(
            session,
            model_service,
            resolved_semantic_parse_service,
            search_tool,
            history_rounds=resolved_config.history_rounds,
        )
    resolved_result_artifact_service = (
        result_artifact_service or build_result_artifact_service(session)
    )
    metrics_recorder = build_metrics_recorder()
    tool_services = AgentToolContextServices(
        result_artifact_service=resolved_result_artifact_service,
        result_store=ResultStore(resolved_result_artifact_service),
    )
    state_factory = AgentRuntimeStateFactory(
        session,
        current_user.id,
        resolved_config,
        tool_services,
        cancellation_signal_factory,
    )
    if query_task_executor is None:
        session_bind = session.get_bind()
        worker_engine = getattr(session_bind, "engine", session_bind)
        resolved_query_task_executor = QueryTaskExecutor(
            lambda: Session(worker_engine),
            lambda worker_session: build_query_service(
                worker_session,
                default_limit=resolved_config.default_limit,
                sample_rows=resolved_config.sample_rows,
                max_transient_retries=resolved_config.query_transient_retries,
            ),
        )
    else:
        resolved_query_task_executor = query_task_executor
    return RunOrchestrator(
        session,
        event_publisher=resolved_publisher,
        recorder=resolved_recorder,
        lifecycle=lifecycle,
        input_preparer=resolved_input_preparer,
        state_factory=state_factory,
        mode_router=ModeRouter(
            resolved_semantic_schema_provider,
            resolved_limited_multistep_decomposer,
        ),
        fast_pipeline=FastPipeline(
            FastPipelineDependencies(
                registry=resolved_registry,
                result_processor=result_processor,
                finalization_service=resolved_finalization_service,
                lifecycle=lifecycle,
                event_publisher=resolved_publisher,
                session=session,
                answer_composer=resolved_answer_composer,
                assisted_fallback_service=resolved_assisted_fallback,
                assisted_fallback_enabled=resolved_config.assisted_fallback_enabled,
                semantic_schema_provider=resolved_semantic_schema_provider,
                metrics=metrics_recorder,
                trace_recorder=resolved_recorder,
            )
        ),
        plan_pipeline=PlanPipeline(
            PlanPipelineDependencies(
                registry=resolved_registry,
                result_processor=result_processor,
                finalization_service=resolved_finalization_service,
                lifecycle=lifecycle,
                event_publisher=resolved_publisher,
                session=session,
                max_query_tasks=resolved_config.plan_max_query_tasks,
                query_task_executor=resolved_query_task_executor,
                query_concurrency=resolved_config.plan_query_concurrency,
                query_timeout_seconds=resolved_config.tool_timeout_seconds,
                compute_engine=ComputeEngine(),
                compute_enabled=resolved_config.compute_enabled,
                answer_composer=resolved_answer_composer,
                metrics=metrics_recorder,
                trace_recorder=resolved_recorder,
            )
        ),
    )


__all__ = ["build_run_orchestrator", "build_agent_tool_registry"]
