from sqlmodel import Session

from apps.access_control.data_policy import SessionDataPolicyProvider
from apps.capabilities.sql.executor import SqlExecuteTool
from apps.chatbi.composition import (
    build_semantic_query_service,
    build_semantic_retrieval_service,
)
from apps.chatbi.services.sql_permission import PermissionAdapter
from apps.retrieval.service import build_retrieval_service
from apps.semantic.repository.sqlmodel.schema_loader import SemanticSchemaLoader
from apps.semantic.services.schema_service import SemanticSchemaService
from apps.workflow.capabilities.adapters.answer import (
    AnswerAdapter,
    AnswerModelClient,
)
from apps.workflow.capabilities.adapters.interaction import (
    InteractionAdapter,
)
from apps.workflow.capabilities.adapters.knowledge import (
    SemanticKnowledgeAdapter,
)
from apps.workflow.capabilities.adapters.question import (
    QuestionAdapter,
    QuestionClassificationModelClient,
)
from apps.workflow.capabilities.adapters.sql import SqlAdapter
from apps.workflow.capabilities.execution import SessionSqlExecutionGateway
from apps.workflow.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)
from apps.workflow.capabilities.real import RealChatBICapabilityGateway
from apps.workflow.conditions.core import register_chatbi_conditions
from apps.workflow.definitions.chatbi_minimal_v1 import (
    build_chatbi_minimal_definition,
    register_chatbi_minimal_handlers,
)
from apps.workflow.definitions.chatbi_v1 import (
    build_chatbi_v1_definition,
    register_chatbi_v1_handlers,
)
from apps.workflow_engine.infrastructure.artifacts.file_store import (
    FileArtifactStore,
    SessionArtifactMetadataStore,
    workflow_artifact_root,
)
from apps.workflow_engine.infrastructure.events.publisher import DatabaseEventPublisher
from apps.workflow_engine.infrastructure.persistence.interaction_manager import (
    DatabaseInteractionManager,
)
from apps.workflow_engine.infrastructure.persistence.node_execution_repository import (
    NodeExecutionRepository,
)
from apps.workflow_engine.infrastructure.persistence.run_repository import RunRepository
from apps.workflow_engine.ports.run_store import RunStore
from apps.workflow_engine.registry.condition_registry import ConditionRegistry
from apps.workflow_engine.registry.definition_validator import DefinitionValidator
from apps.workflow_engine.registry.handler_registry import HandlerRegistry
from apps.workflow_engine.registry.workflow_registry import WorkflowRegistry
from apps.workflow_engine.runtime.checkpoint_manager import CheckpointManager
from apps.workflow_engine.runtime.context_patcher import ContextPatcher
from apps.workflow_engine.runtime.graph_runtime import GraphRuntime
from apps.workflow_engine.runtime.lease import InMemoryRunLease
from apps.workflow_engine.runtime.router import ConditionRouter
from apps.workflow_engine.runtime.scheduler import NodeScheduler
from common.core.db import engine


def build_placeholder_chatbi_runtime(session: Session, commit_events: bool = False) -> GraphRuntime:
    """组装可同步执行的 ChatBI 最小图运行时。"""

    gateway = PlaceholderChatBICapabilityGateway()
    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_minimal_handlers(handlers, gateway)
    register_chatbi_conditions(conditions)

    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(build_chatbi_minimal_definition())

    run_store = RunRepository(session)
    events = DatabaseEventPublisher(session, commit_on_publish=commit_events)
    return GraphRuntime(
        registry=registry,
        run_store=run_store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=CheckpointManager(run_store, events),
        lease=InMemoryRunLease(),
        node_execution_recorder=NodeExecutionRepository(session),
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

    schema_provider = SemanticSchemaService(SemanticSchemaLoader(session))
    retrieval_service = build_retrieval_service(
        session, schema_provider=schema_provider
    )

    def session_factory() -> Session:
        """为并行执行与 artifact 元数据写入创建独立会话。"""

        return Session(engine)

    artifact_store = FileArtifactStore(
        root=workflow_artifact_root(),
        metadata_store=SessionArtifactMetadataStore(session_factory),
    )
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
                execute_tool_factory=SqlExecuteTool,
            ),
            permission_adapter=PermissionAdapter(
                policy_provider=SessionDataPolicyProvider(session_factory)
            ),
            artifact_store=artifact_store,
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
    gateway,
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
    effective_run_store = run_store if run_store is not None else RunRepository(session)
    events = DatabaseEventPublisher(session, commit_on_publish=commit_events)
    return GraphRuntime(
        registry=registry,
        run_store=effective_run_store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=CheckpointManager(effective_run_store, events),
        lease=InMemoryRunLease(),
        interaction_manager=DatabaseInteractionManager(session),
        node_execution_recorder=NodeExecutionRepository(session),
    )
