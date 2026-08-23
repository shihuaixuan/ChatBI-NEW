"""ChatBI Agent 运行时组装入口。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from sqlmodel import Session

from apps.chatbi.adapters.prompts.limited_multistep import (
    DefaultLimitedMultiStepPromptBuilder,
)
from apps.chatbi.adapters.prompts.research_policy import (
    DefaultResearchPolicyPromptBuilder,
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
from apps.chatbi.orchestration.pipeline.research import (
    ResearchPipeline,
    ResearchPipelineDependencies,
)
from apps.chatbi.orchestration.pipeline.research_agent_pipeline import (
    ResearchAgentPipeline,
    ResearchAgentPipelineDependencies,
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
from apps.chatbi.services.research.policy import ResearchPolicy
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

if TYPE_CHECKING:
    from apps.chatbi.services.research.shadow import ShadowRunner


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
    resolved_plan_pipeline = PlanPipeline(
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
            semantic_schema_provider=resolved_semantic_schema_provider,
        )
    )
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
        plan_pipeline=resolved_plan_pipeline,
        research_pipeline=ResearchPipeline(
            ResearchPipelineDependencies(
                policy=ResearchPolicy(
                    model_service,
                    DefaultResearchPolicyPromptBuilder(),
                ),
                plan_pipeline=resolved_plan_pipeline,
                lifecycle=lifecycle,
                session=session,
            )
        ),
        shadow_runner=(
            build_shadow_runner(session, resolved_config)
            if resolved_config.research_execution_mode == "shadow"
            else None
        ),
        # 阶段 7.5：agent 引擎主路径管道；仅在显式配置 agent 时装配。
        research_agent_pipeline=(
            build_research_agent_pipeline(
                session,
                resolved_config,
                lifecycle=lifecycle,
                event_publisher=resolved_publisher,
                registry=resolved_registry,
                query_task_executor=resolved_query_task_executor,
                artifact_service=resolved_result_artifact_service,
                recorder=resolved_recorder,
            )
            if resolved_config.research_execution_mode == "agent"
            else None
        ),
    )


class _ShadowLifecycleStub:
    """shadow 栈内禁用生命周期交互：双跑要么自主完成要么显式失败。

    AnalysisExecutionService 只在取消和语义澄清挂起时触达生命周期；
    shadow 使用 NeverCancelled 且不允许挂起等待用户，因此这两个入口
    理论上不可达。显式报错而不是静默吞掉，避免误写主路径状态。
    """

    @staticmethod
    def _refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise NotImplementedError("RESEARCH_SHADOW_LIFECYCLE_UNAVAILABLE")

    cancel = _refuse
    suspend = _refuse


def build_shadow_runner(
    session: Any,
    config: AgentConfig,
    *,
    recorder: AgentTraceRecorder | None = None,
) -> ShadowRunner:
    """装配生产 shadow 双跑栈（阶段 7 §11.3.1）。

    模型客户端、提示词等无会话服务在此创建一次；数据库会话、事件发布、
    Trace 和查询服务在每次双跑时针对 shadow 会话重建——SQLAlchemy 会话
    不能跨线程共享，且所有影子副作用必须落在 shadow run 身份下。
    """

    from apps.chatbi.orchestration.agent.model_client import DefaultAgentModelClient
    from apps.chatbi.orchestration.pipeline.research_agent import ResearchAgentHarness
    from apps.chatbi.services.execution.analysis_execution import (
        AnalysisExecutionDependencies,
        AnalysisExecutionService,
    )
    from apps.chatbi.services.research.semantic_runtime import SemanticQueryRuntime
    from apps.chatbi.services.research.shadow import (
        ResearchExecutionState,
        ShadowRunner,
    )

    resolved_recorder = recorder or build_agent_trace_recorder()

    def session_factory() -> Any:
        bind = session.get_bind()
        worker_engine = getattr(bind, "engine", bind)
        return Session(worker_engine)

    def harness_factory(
        shadow_session: Any,
        run_row: Any,
        record: Any,
        _requirement: Any,
        *,
        context_state_overlay: dict[str, Any] | None = None,
    ) -> ResearchAgentHarness:
        publisher = EventPublisher(shadow_session)
        query_service = build_query_service(
            shadow_session,
            default_limit=config.default_limit,
            sample_rows=config.sample_rows,
            max_transient_retries=config.query_transient_retries,
        )
        semantic_query_service = build_semantic_sql_compilation_service(shadow_session)
        artifact_service = build_result_artifact_service(shadow_session)
        # 执行服务只需要计划侧的证明与编译工具；研究四工具由 Harness 自建。
        registry = ToolRegistry(middlewares=default_middlewares())
        registry.register(ValidateSqlTool(query_service))
        registry.register(CompileSemanticSqlTool(semantic_query_service, query_service))
        bind = shadow_session.get_bind()
        worker_engine = getattr(bind, "engine", bind)
        query_task_executor = QueryTaskExecutor(
            lambda: Session(worker_engine),
            lambda worker_session: build_query_service(
                worker_session,
                default_limit=config.default_limit,
                sample_rows=config.sample_rows,
                max_transient_retries=config.query_transient_retries,
            ),
        )
        execution_service = AnalysisExecutionService(
            AnalysisExecutionDependencies(
                registry=registry,
                result_processor=ChatBIToolResultProcessor(),
                # shadow 栈内生命周期交互显式不可用（见 _ShadowLifecycleStub）。
                lifecycle=cast("AgentLifecycle", _ShadowLifecycleStub()),
                event_publisher=publisher,
                session=shadow_session,
                query_task_executor=query_task_executor,
                max_query_tasks=config.plan_max_query_tasks,
                query_concurrency=config.plan_query_concurrency,
                query_timeout_seconds=config.tool_timeout_seconds,
                compute_engine=ComputeEngine(),
                compute_enabled=config.compute_enabled,
                trace_recorder=resolved_recorder,
            )
        )
        semantic_runtime = SemanticQueryRuntime(
            execution_service,
            execution_state_factory=lambda ctx: ResearchExecutionState(
                ctx=ctx, run=run_row, record=record
            ),
        )
        return ResearchAgentHarness(
            session=shadow_session,
            config=config,
            run_row=run_row,
            record=record,
            model_client=DefaultAgentModelClient(),
            semantic_runtime=semantic_runtime,
            compute_engine=ComputeEngine(),
            result_store=ResultStore(artifact_service),
            recorder=resolved_recorder,
            # shadow 与主路径同口径的边界输入：盖戳后的 semantic_scope 等
            # （run 1306 教训：缺 overlay 时每条查询都死在 SEMANTIC_SCOPE_REQUIRED）。
            context_state_overlay=context_state_overlay,
        )

    return ShadowRunner(
        session_factory=session_factory,
        harness_factory=harness_factory,
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
) -> ResearchAgentPipeline:
    """装配主路径 Research Agent 管道（阶段 7.5 切流接线）。

    与 shadow 栈（逐次重建会话与服务）不同：复用请求作用域的会话、
    工具注册表和生命周期——主路径与用户可见执行共享同一事务边界，
    取消与澄清挂起真实可达。执行服务只需要计划侧的证明与编译工具
    （研究四工具由 Harness 自建），``resolved_registry`` 天然满足。
    """

    from apps.chatbi.orchestration.agent.model_client import DefaultAgentModelClient
    from apps.chatbi.services.execution.analysis_execution import (
        AnalysisExecutionDependencies,
        AnalysisExecutionService,
    )
    from apps.chatbi.services.research.semantic_runtime import SemanticQueryRuntime
    from apps.chatbi.services.research.shadow import ResearchExecutionState

    resolved_recorder = recorder or build_agent_trace_recorder()
    execution_service = AnalysisExecutionService(
        AnalysisExecutionDependencies(
            registry=registry,
            result_processor=ChatBIToolResultProcessor(),
            lifecycle=lifecycle,
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
        )
    )


__all__ = [
    "build_run_orchestrator",
    "build_agent_tool_registry",
    "build_shadow_runner",
    "build_research_agent_pipeline",
]
