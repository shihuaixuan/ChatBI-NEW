from sqlmodel import Session

from apps.access_control.data_policy import SessionDataPolicyProvider
from apps.chatbi.adapters.execution import DatasourceQueryExecutor
from apps.chatbi.composition import (
    build_result_artifact_service,
    build_semantic_query_service,
    build_semantic_retrieval_service,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.answer import AnswerAdapter
from apps.chatbi.orchestration.graph.capabilities.adapters.interaction import (
    InteractionAdapter,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.knowledge import (
    SemanticKnowledgeAdapter,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.question import (
    QuestionAdapter,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.question_common import (
    QuestionClassificationModelClient,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.sql import SqlAdapter
from apps.chatbi.orchestration.graph.capabilities.execution import (
    SessionSqlExecutionGateway,
)
from apps.chatbi.orchestration.graph.capabilities.gateway import (
    ChatBICapabilityGateway,
)
from apps.chatbi.orchestration.graph.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)
from apps.chatbi.orchestration.graph.capabilities.real import (
    RealChatBICapabilityGateway,
)
from apps.chatbi.orchestration.graph.conditions.core import register_chatbi_conditions
from apps.chatbi.orchestration.graph.definitions.chatbi_minimal_v1 import (
    build_chatbi_minimal_definition,
    register_chatbi_minimal_handlers,
)
from apps.chatbi.orchestration.graph.definitions.chatbi_v1 import (
    build_chatbi_v1_definition,
    register_chatbi_v1_handlers,
)
from apps.chatbi.services.execution.sql_permission import SQLPermissionService
from apps.chatbi.services.generation.answer_generation import AnswerModelClient
from apps.retrieval.service import build_retrieval_service
from apps.semantic.composition import build_semantic_schema_service
from common.core.db import engine
from sqlbot_platform.workflow_engine.composition import (
    build_persistent_runtime_services,
)
from sqlbot_platform.workflow_engine.ports.run_store import RunStore
from sqlbot_platform.workflow_engine.registry.condition_registry import (
    ConditionRegistry,
)
from sqlbot_platform.workflow_engine.registry.definition_validator import (
    DefinitionValidator,
)
from sqlbot_platform.workflow_engine.registry.handler_registry import HandlerRegistry
from sqlbot_platform.workflow_engine.registry.workflow_registry import WorkflowRegistry
from sqlbot_platform.workflow_engine.runtime.checkpoint_manager import CheckpointManager
from sqlbot_platform.workflow_engine.runtime.context_patcher import ContextPatcher
from sqlbot_platform.workflow_engine.runtime.graph_runtime import GraphRuntime
from sqlbot_platform.workflow_engine.runtime.lease import InMemoryRunLease
from sqlbot_platform.workflow_engine.runtime.router import ConditionRouter
from sqlbot_platform.workflow_engine.runtime.scheduler import NodeScheduler


def build_placeholder_chatbi_runtime(session: Session, commit_events: bool = False) -> GraphRuntime:
    """组装可同步执行的 ChatBI 最小图运行时。"""

    gateway = PlaceholderChatBICapabilityGateway()
    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_minimal_handlers(handlers, gateway)
    register_chatbi_conditions(conditions)

    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(build_chatbi_minimal_definition())

    persistence = build_persistent_runtime_services(
        session,
        commit_events=commit_events,
    )
    return GraphRuntime(
        registry=registry,
        run_store=persistence.run_store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=CheckpointManager(
            persistence.run_store,
            persistence.event_publisher,
        ),
        lease=InMemoryRunLease(),
        node_execution_recorder=persistence.node_execution_recorder,
    )


def build_placeholder_chatbi_v1_runtime(session: Session, commit_events: bool = False) -> GraphRuntime:
    """组装可同步执行的 ChatBI v1 占位图运行时。"""

    gateway = PlaceholderChatBICapabilityGateway()
    return _build_chatbi_v1_runtime(session, gateway, commit_events=commit_events)


def build_real_chatbi_v1_runtime(
    session: Session,
    question_model_client: QuestionClassificationModelClient | None = None,
    answer_model_client: AnswerModelClient | None = None,
    commit_events: bool = False,
    run_store: RunStore | None = None,
) -> GraphRuntime:
    """组装真实 classify_question + 其他占位能力回退的 ChatBI v1 运行时。"""

    schema_provider = build_semantic_schema_service(session)
    retrieval_service = build_retrieval_service(
        session, schema_provider=schema_provider
    )

    def session_factory() -> Session:
        """为并行执行创建独立会话。"""

        return Session(engine)

    result_artifact_service = build_result_artifact_service(session)
    gateway = RealChatBICapabilityGateway(
        question_adapter=QuestionAdapter(
            model_client=question_model_client,
            schema_provider=schema_provider,
        ),
        answer_adapter=AnswerAdapter(model_client=answer_model_client),
        knowledge_adapter=SemanticKnowledgeAdapter(
            semantic_retrieval_service=build_semantic_retrieval_service(
                session,
                retrieval_gateway=retrieval_service,
            ),
        ),
        interaction_adapter=InteractionAdapter(schema_provider=schema_provider),
        sql_adapter=SqlAdapter(
            semantic_query_service=build_semantic_query_service(session),
            execute_tool=SessionSqlExecutionGateway(
                session_factory,
                execute_tool_factory=DatasourceQueryExecutor,
            ),
            permission_adapter=SQLPermissionService(
                policy_provider=SessionDataPolicyProvider(session_factory)
            ),
            result_artifact_service=result_artifact_service,
        ),
        fallback_gateway=PlaceholderChatBICapabilityGateway(),
    )
    return _build_chatbi_v1_runtime(
        session,
        gateway,
        commit_events=commit_events,
        run_store=run_store,
    )


def _build_chatbi_v1_runtime(
    session: Session,
    gateway: ChatBICapabilityGateway,
    commit_events: bool = False,
    run_store: RunStore | None = None,
) -> GraphRuntime:
    """组装 ChatBI v1 图运行时。"""

    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_v1_handlers(handlers, gateway)
    register_chatbi_conditions(conditions)

    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(build_chatbi_v1_definition())

    # 应用层可注入带聊天历史投影的仓储，独立执行仍使用默认仓储。
    persistence = build_persistent_runtime_services(
        session,
        commit_events=commit_events,
        run_store=run_store,
    )
    return GraphRuntime(
        registry=registry,
        run_store=persistence.run_store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=CheckpointManager(
            persistence.run_store,
            persistence.event_publisher,
        ),
        lease=InMemoryRunLease(),
        interaction_manager=persistence.interaction_store,
        node_execution_recorder=persistence.node_execution_recorder,
    )
