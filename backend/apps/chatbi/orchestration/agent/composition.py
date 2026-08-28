"""ChatBI Agent 运行时组装入口。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from sqlmodel import Session

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
from apps.chatbi.orchestration.agent.model_client import DefaultAgentModelClient
from apps.chatbi.orchestration.agent.preparation import AgentInputPreparer
from apps.chatbi.orchestration.agent.run_orchestrator import RunOrchestrator
from apps.chatbi.orchestration.agent.state import AgentRuntimeStateFactory
from apps.chatbi.orchestration.agent.tool_results import ChatBIToolResultProcessor
from apps.chatbi.orchestration.agent.tools.base import AgentToolContextServices
from apps.chatbi.orchestration.agent.tools.core import FinishTool
from apps.chatbi.orchestration.agent.tools.interaction import ClarifyTool
from apps.chatbi.orchestration.agent.tools.temporal import ParseTimeRangeTool
from apps.chatbi.orchestration.pipeline.mode_router import ExecutionRequirementBuilder
from apps.chatbi.orchestration.pipeline.research_agent_pipeline import (
    ResearchAgentPipeline,
    ResearchAgentPipelineDependencies,
)
from apps.chatbi.services.computation import ComputeEngine
from apps.chatbi.services.execution import (
    AnalysisExecutionDependencies,
    AnalysisExecutionService,
    QueryTaskExecutor,
    ResultArtifactService,
    ResultStore,
)
from apps.chatbi.services.generation.agent_finalization import AgentFinalizationService
from apps.chatbi.services.generation.answer_composer import AnswerComposer
from apps.chatbi.services.planning import PhysicalSchemaService
from apps.chatbi.services.ports import AnalysisExecutionLifecycle
from apps.chatbi.services.research.semantic_runtime import (
    ResearchExecutionState,
    SemanticQueryRuntime,
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
    memory_service: MemoryService | None = None,
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
    # 重写、语义解析和回答阶段共享同一个结构化模型服务。
    model_service = build_question_model_service(enforce_json=True)
    if finalization_service is None:
        resolved_finalization_service = AgentFinalizationService(model_service)
        resolved_answer_composer: AnswerComposer | None = (
            answer_composer
            or AnswerComposer(
                model_service,
                citation_enforced=resolved_config.answer_citation_enforced,
            )
        )
    else:
        resolved_finalization_service = finalization_service
        resolved_answer_composer = answer_composer
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
        execution_requirement_builder=ExecutionRequirementBuilder(
            resolved_semantic_schema_provider,
        ),
        # 所有分析请求无条件进入同一个 Research Agent ReAct Runtime。
        research_agent_pipeline=build_research_agent_pipeline(
            session,
            resolved_config,
            lifecycle=lifecycle,
            event_publisher=resolved_publisher,
            registry=resolved_registry,
            query_task_executor=resolved_query_task_executor,
            artifact_service=resolved_result_artifact_service,
            recorder=resolved_recorder,
            semantic_retrieval_service=resolved_semantic_retrieval_service,
        ),
    )


def build_research_agent_pipeline(
    session: Any,
    config: AgentConfig,
    *,
    lifecycle: AgentLifecycle,
    event_publisher: EventPublisher,
    registry: ToolRegistry,
    query_task_executor: QueryTaskExecutor,
    artifact_service: ResultArtifactService,
    recorder: AgentTraceRecorder | None = None,
    semantic_retrieval_service: Any = None,
) -> ResearchAgentPipeline:
    """装配 Evidence 驱动 Research Agent Runtime。

    与 shadow 栈（逐次重建会话与服务）不同：复用请求作用域的会话、
    工具注册表和生命周期——主路径与用户可见执行共享同一事务边界，
    取消与澄清挂起真实可达。执行服务只需要计划侧的证明与编译工具
    （研究四工具由 Harness 自建），``resolved_registry`` 天然满足。
    """

    resolved_recorder = recorder or build_agent_trace_recorder()
    execution_service = AnalysisExecutionService(
        AnalysisExecutionDependencies(
            registry=registry,
            result_processor=ChatBIToolResultProcessor(),
            lifecycle=cast(AnalysisExecutionLifecycle, lifecycle),
            event_publisher=event_publisher,
            session=session,
            query_task_executor=query_task_executor,
            max_query_tasks=config.plan_max_query_tasks,
            query_concurrency=config.plan_query_concurrency,
            query_timeout_seconds=config.tool_timeout_seconds,
            compute_engine=ComputeEngine(),
            compute_enabled=config.compute_enabled,
            trace_recorder=resolved_recorder,
        )
    )
    return ResearchAgentPipeline(
        ResearchAgentPipelineDependencies(
            config=config,
            session=session,
            lifecycle=lifecycle,
            model_client=DefaultAgentModelClient(),
            result_store=ResultStore(artifact_service),
            recorder=resolved_recorder,
            semantic_runtime_factory=lambda run_row, record: SemanticQueryRuntime(
                execution_service,
                execution_state_factory=lambda ctx: ResearchExecutionState(
                    ctx=ctx, run=run_row, record=record
                ),
            ),
            compute_engine_factory=ComputeEngine,
            semantic_retrieval_service=semantic_retrieval_service,
        )
    )


__all__ = [
    "build_run_orchestrator",
    "build_agent_tool_registry",
    "build_research_agent_pipeline",
]
