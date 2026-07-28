"""ChatBI Agent 运行时组装入口。"""

from __future__ import annotations

from typing import Any

from apps.chatbi.composition import (
    build_agent_event_publisher,
    build_agent_tracer,
    build_chat_record_service,
    build_physical_schema_service,
    build_query_service,
    build_question_understanding_service,
    build_result_artifact_service,
    build_semantic_query_service,
    build_semantic_retrieval_service,
)
from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.loop import AgentLoop
from apps.chatbi.orchestration.agent.model_client import DefaultAgentModelClient
from apps.chatbi.orchestration.agent.preparation import AgentInputPreparer
from apps.chatbi.orchestration.agent.reasoning import AgentModelClient, AgentReasoner
from apps.chatbi.orchestration.agent.state import AgentRuntimeStateFactory
from apps.chatbi.orchestration.agent.tool_execution import AgentToolExecutor
from apps.chatbi.orchestration.agent.tools.base import AgentToolContextServices
from apps.chatbi.orchestration.agent.tools.core import build_default_tools
from apps.chatbi.orchestration.agent.tools.interaction import (
    ClarifyTool,
    GetSqlExamplesTool,
    SearchTerminologyTool,
)
from apps.chatbi.services.execution import ResultArtifactService
from apps.chatbi.services.planning import (
    PhysicalSchemaService,
    SemanticCompilationService,
    SemanticRetrievalService,
)
from apps.chatbi.services.understanding import QuestionUnderstandingService
from apps.datasource.services import DatasourceQueryService
from apps.event import EventPublisher
from apps.semantic.composition import build_semantic_term_query_service
from apps.semantic.services.term_query_service import SemanticTermQueryService
from apps.tool import ToolRegistry, default_middlewares
from apps.trace import AgentTracer


def build_agent_tool_registry(config: AgentConfig) -> ToolRegistry:
    """装配 Agent 默认工具集合及执行中间件。"""

    timeout = float(getattr(config, "tool_timeout_seconds", 60) or 60)
    registry = ToolRegistry(
        middlewares=default_middlewares(timeout_seconds=timeout)
    )
    for tool in build_default_tools():
        registry.register(tool)
    registry.register(ClarifyTool())
    registry.register(SearchTerminologyTool())
    registry.register(GetSqlExamplesTool())
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
    semantic_query_service: SemanticCompilationService | None = None,
    semantic_retrieval_service: SemanticRetrievalService | None = None,
    physical_schema_service: PhysicalSchemaService | None = None,
    result_artifact_service: ResultArtifactService | None = None,
    event_publisher: EventPublisher | None = None,
    tracer: AgentTracer | None = None,
    reasoner: AgentReasoner | None = None,
    tool_executor: AgentToolExecutor | None = None,
    input_preparer: AgentInputPreparer | None = None,
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
    resolved_registry = registry or build_agent_tool_registry(resolved_config)
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
    )
    resolved_understanding_service = (
        understanding_service or build_question_understanding_service()
    )
    resolved_input_preparer = input_preparer or AgentInputPreparer(
        session,
        resolved_config,
        resolved_understanding_service,
        lifecycle,
        resolved_publisher,
    )
    tool_services = AgentToolContextServices(
        term_query_service=(
            term_query_service or build_semantic_term_query_service(session)
        ),
        query_service=query_service
        or build_query_service(
            session,
            default_limit=resolved_config.default_limit,
            sample_rows=resolved_config.sample_rows,
        ),
        semantic_query_service=(
            semantic_query_service or build_semantic_query_service(session)
        ),
        semantic_retrieval_service=(
            semantic_retrieval_service
            or build_semantic_retrieval_service(session)
        ),
        physical_schema_service=(
            physical_schema_service or build_physical_schema_service(session)
        ),
        result_artifact_service=(
            result_artifact_service or build_result_artifact_service(session)
        ),
    )
    state_factory = AgentRuntimeStateFactory(
        session,
        current_user.id,
        resolved_config,
        tool_services,
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
