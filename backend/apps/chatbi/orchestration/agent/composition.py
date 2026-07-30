"""ChatBI Agent 运行时组装入口。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.chatbi.composition import (
    build_agent_event_publisher,
    build_agent_tracer,
    build_chat_record_service,
    build_physical_schema_service,
    build_query_service,
    build_question_understanding_service,
    build_result_artifact_service,
)
from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.loop import AgentLoop
from apps.chatbi.orchestration.agent.model_client import DefaultAgentModelClient
from apps.chatbi.orchestration.agent.preparation import AgentInputPreparer
from apps.chatbi.orchestration.agent.reasoning import AgentModelClient, AgentReasoner
from apps.chatbi.orchestration.agent.state import AgentRuntimeStateFactory
from apps.chatbi.orchestration.agent.tool_execution import AgentToolExecutor
from apps.chatbi.orchestration.agent.tool_results import ChatBIToolResultProcessor
from apps.chatbi.orchestration.agent.tools.base import AgentToolContextServices
from apps.chatbi.orchestration.agent.tools.core import FinishTool
from apps.chatbi.orchestration.agent.tools.interaction import ClarifyTool
from apps.chatbi.services.execution import ResultArtifactService
from apps.chatbi.services.planning import PhysicalSchemaService
from apps.chatbi.services.understanding import QuestionUnderstandingService
from apps.datasource.services import DatasourceQueryService
from apps.event import EventPublisher
from apps.knowledge.composition import build_sql_example_query_service
from apps.knowledge.services.sql_example_query_service import SQLExampleQueryService
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
from apps.trace import AgentTracer


def build_agent_tool_registry(
    *,
    query_service: DatasourceQueryService,
    semantic_query_service: SemanticSQLCompilationService,
    semantic_retrieval_service: RetrievalService,
    physical_schema_service: PhysicalSchemaService,
    term_query_service: SemanticTermQueryService,
    sql_example_query_service: SQLExampleQueryService,
) -> ToolRegistry:
    """装配 Agent 默认工具集合及执行中间件。"""

    registry = ToolRegistry(middlewares=default_middlewares())
    registry.register(
        SearchSemanticAssetsTool(semantic_retrieval_service, query_service)
    )
    registry.register(CompileSemanticSqlTool(semantic_query_service, query_service))
    registry.register(FinishTool())
    registry.register(ClarifyTool())
    registry.register(GetDatasetSchemaTool(physical_schema_service))
    registry.register(ValidateSqlTool(query_service))
    registry.register(ExecuteSqlTool(query_service))
    registry.register(SearchTerminologyTool(term_query_service))
    registry.register(GetSqlExamplesTool(sql_example_query_service))
    return registry


def build_agent_loop(
    session: Any,
    current_user: Any,
    config: AgentConfig | None = None,
    *,
    model_client: AgentModelClient | None = None,
    registry: ToolRegistry | None = None,
    understanding_service: QuestionUnderstandingService | None = None,
    term_query_service: SemanticTermQueryService | None = None,
    query_service: DatasourceQueryService | None = None,
    semantic_query_service: SemanticSQLCompilationService | None = None,
    semantic_retrieval_service: RetrievalService | None = None,
    semantic_schema_provider: DatasetSchemaProvider | None = None,
    physical_schema_service: PhysicalSchemaService | None = None,
    sql_example_query_service: SQLExampleQueryService | None = None,
    result_artifact_service: ResultArtifactService | None = None,
    event_publisher: EventPublisher | None = None,
    tracer: AgentTracer | None = None,
    reasoner: AgentReasoner | None = None,
    tool_executor: AgentToolExecutor | None = None,
    input_preparer: AgentInputPreparer | None = None,
    cancellation_signal_factory: Callable[[int], CancellationSignal] | None = None,
) -> AgentLoop:
    """构造依赖完整的 AgentLoop；生产入口和测试统一使用此函数。"""

    resolved_config = config or AgentConfig()
    resolved_publisher = event_publisher or build_agent_event_publisher(session)
    resolved_tracer = tracer or build_agent_tracer()
    lifecycle = AgentLifecycle(
        session,
        current_user.id,
        build_chat_record_service(session),
        resolved_publisher,
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
    resolved_registry = registry or build_agent_tool_registry(
        query_service=resolved_query_service,
        semantic_query_service=resolved_semantic_query_service,
        semantic_retrieval_service=resolved_semantic_retrieval_service,
        physical_schema_service=resolved_physical_schema_service,
        term_query_service=resolved_term_query_service,
        sql_example_query_service=resolved_sql_example_query_service,
    )
    resolved_reasoner = reasoner or AgentReasoner(
        resolved_config,
        model_client or DefaultAgentModelClient(),
        resolved_registry,
        resolved_tracer,
    )
    resolved_tool_executor = tool_executor or AgentToolExecutor(
        session,
        resolved_config,
        resolved_registry,
        resolved_tracer,
        lifecycle,
        resolved_publisher,
        ChatBIToolResultProcessor(),
    )
    resolved_understanding_service = (
        understanding_service
        or build_question_understanding_service(resolved_semantic_schema_provider)
    )
    resolved_input_preparer = input_preparer or AgentInputPreparer(
        session,
        resolved_config,
        resolved_understanding_service,
        resolved_semantic_schema_provider,
        lifecycle,
        resolved_publisher,
    )
    tool_services = AgentToolContextServices(
        result_artifact_service=(
            result_artifact_service or build_result_artifact_service(session)
        ),
    )
    state_factory = AgentRuntimeStateFactory(
        session,
        current_user.id,
        resolved_config,
        tool_services,
        cancellation_signal_factory,
    )
    return AgentLoop(
        session,
        event_publisher=resolved_publisher,
        tracer=resolved_tracer,
        lifecycle=lifecycle,
        reasoner=resolved_reasoner,
        tool_executor=resolved_tool_executor,
        input_preparer=resolved_input_preparer,
        state_factory=state_factory,
    )


__all__ = ["build_agent_loop", "build_agent_tool_registry"]
