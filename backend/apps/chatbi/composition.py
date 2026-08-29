from collections.abc import Callable

from sqlmodel import Session

from apps.access_control.composition import build_data_policy_service
from apps.access_control.data_policy import SessionDatasourceQueryPolicyProvider
from apps.assistant.composition import build_assistant_service
from apps.chatbi.adapters.agent_trace import (
    ChatBITraceDetailGateway,
    ChatBITraceRepository,
)
from apps.chatbi.adapters.artifact_store import build_workflow_artifact_gateway
from apps.chatbi.adapters.embedding_ranking import (
    EmbeddingDatasourceSelectionCandidateRanker,
    EmbeddingSchemaRankingClient,
)
from apps.chatbi.adapters.execution_cleanup import (
    CommittedAgentCleanupGateway,
    WorkflowArtifactCleanupGateway,
)
from apps.chatbi.adapters.question_model import build_question_model_service
from apps.chatbi.services.conversation import (
    resolve_conversation_binding,
)
from apps.chatbi.services.conversation.chat_application import (
    ChatApplicationService,
)
from apps.chatbi.services.conversation.deletion_service import (
    ChatDeletionService,
)
from apps.chatbi.services.conversation.history_reader import (
    ConversationHistoryReader,
)
from apps.chatbi.services.conversation.ports import ExecutionCleanupGateway
from apps.chatbi.services.execution import ResultArtifactService
from apps.chatbi.services.generation import (
    GenerationContextService,
    SchemaContextService,
)
from apps.chatbi.services.planning import (
    DatasourceSelectionCandidateService,
    PhysicalSchemaService,
)
from apps.chatbi.services.understanding import (
    QuestionUnderstandingService,
    SemanticParseService,
    StructuredModelService,
)
from apps.conversation import (
    ChatLogService,
    ChatRecordService,
    ConversationBinding,
    ConversationService,
    HistoryQueryService,
)
from apps.conversation.composition import (
    build_chat_log_service as build_conversation_chat_log_service,
)
from apps.conversation.composition import (
    build_chat_record_service as build_conversation_chat_record_service,
)
from apps.conversation.composition import (
    build_conversation_service as build_owned_conversation_service,
)
from apps.conversation.composition import (
    build_history_query_service as build_conversation_history_query_service,
)
from apps.datasource.composition import (
    build_datasource_connection_service,
    build_datasource_metadata_service,
    build_datasource_query_service,
    build_datasource_service,
)
from apps.datasource.services import DatasourceQueryService
from apps.event import EventPublisher
from apps.knowledge.composition import build_sql_example_query_service
from apps.knowledge.recommended import build_recommended_problem_service
from apps.semantic.composition import (
    build_semantic_dataset_binding_service,
    build_semantic_dataset_catalog_service,
    build_semantic_term_query_service,
)
from apps.semantic.services.schema_service import DatasetSchemaProvider
from apps.trace import AgentTraceRecorder, TraceConfig, build_trace_exporter
from common.core.config import settings
from common.core.db import engine


def build_agent_event_publisher(session: Session) -> EventPublisher:
    """装配 Agent 产品事件发布器。"""

    return EventPublisher(session)


def build_agent_trace_recorder() -> AgentTraceRecorder:
    """装配全量持久化 Trace 和可选 OpenTelemetry 导出。"""

    def trace_session_factory() -> Session:
        return Session(engine)

    exporter = build_trace_exporter(
        TraceConfig(
            enabled=settings.AGENT_TRACING_ENABLED,
            sample_rate=settings.AGENT_TRACING_SAMPLE_RATE,
            service_name=settings.AGENT_TRACING_SERVICE_NAME,
            endpoint=settings.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT,
        )
    )
    return AgentTraceRecorder(
        ChatBITraceRepository(trace_session_factory),
        exporter,
        ChatBITraceDetailGateway(
            trace_session_factory,
            build_result_artifact_service,
        ),
    )


def build_query_service(
    session: Session,
    *,
    default_limit: int = 100,
    sample_rows: int = 10,
    max_transient_retries: int = 0,
) -> DatasourceQueryService:
    """装配使用真实数据权限策略的 ChatBI 查询服务。"""

    def policy_session_factory() -> Session:
        return Session(engine)

    return build_datasource_query_service(
        session,
        SessionDatasourceQueryPolicyProvider(policy_session_factory),
        default_limit=default_limit,
        sample_rows=sample_rows,
        max_transient_retries=max_transient_retries,
    )


def build_physical_schema_service(session: Session) -> PhysicalSchemaService:
    """装配 Agent 读取物理表字段的 ChatBI 服务。"""

    return PhysicalSchemaService(
        build_datasource_metadata_service(session),
        build_query_service(session),
    )


def build_result_artifact_service(session: Session) -> ResultArtifactService:
    """装配 ChatBI 统一结果 Artifact Service。"""

    return ResultArtifactService(build_workflow_artifact_gateway(session))


def build_generation_context_service(
    session: Session,
) -> GenerationContextService:
    """装配旧 Chat 和后续统一查询流程使用的知识上下文服务。"""

    return GenerationContextService(
        sql_example_service=build_sql_example_query_service(session),
        term_query_service=build_semantic_term_query_service(session),
    )


def build_generation_schema_context_service(
    session: Session,
) -> SchemaContextService:
    """装配旧 Chat 与后续统一查询流程使用的物理 Schema 上下文服务。"""

    return SchemaContextService(
        datasource_service=build_datasource_service(session),
        metadata_service=build_datasource_metadata_service(session),
        connection_service=build_datasource_connection_service(session),
        data_policy_service=build_data_policy_service(session),
        table_ranker=EmbeddingSchemaRankingClient(),
        embedding_enabled=settings.TABLE_EMBEDDING_ENABLED,
        embedding_limit=settings.TABLE_EMBEDDING_COUNT,
    )


def build_datasource_selection_candidate_service(
    session: Session,
) -> DatasourceSelectionCandidateService:
    """装配生成流程使用的数据源候选范围和排序服务。"""

    return DatasourceSelectionCandidateService(
        assistant_service=build_assistant_service(session),
        datasource_service=build_datasource_service(session),
        ranker=EmbeddingDatasourceSelectionCandidateRanker(),
        embedding_enabled=settings.TABLE_EMBEDDING_ENABLED,
        embedding_limit=settings.DS_EMBEDDING_COUNT,
    )


def build_question_understanding_service(
    schema_provider: DatasetSchemaProvider | None = None,
    *,
    trace_recorder: AgentTraceRecorder | None = None,
) -> QuestionUnderstandingService:
    """装配 Agent 使用的严格问题理解服务。"""

    # 结构化模型不设置 max_tokens，避免合法语义输出因固定上限被截断。
    question_model_service = build_question_model_service(
        reasoning_effort=settings.QUERY_UNDERSTANDING_REASONING_EFFORT,
        # 结构化问题理解必须先满足 JSON 传输协议，再交给领域 DTO 校验。
        enforce_json=True,
    )
    temporal_question_model_service = build_question_model_service(
        # Temporal 保留模型自身的输出预算，避免截断合法时间计划。
        question_defaults=False,
        enforce_json=True,
    )
    return QuestionUnderstandingService(
        question_model_service=question_model_service,
        temporal_question_model_service=temporal_question_model_service,
        schema_provider=schema_provider,
        # P1 的生产入口直接使用最终语义契约，不在未上线项目中保留旧链路切换。
        temporal_shadow_enabled=False,
        temporal_authority_enabled=True,
        semantic_repair_v2_enabled=True,
        mention_contract_enabled=True,
        trace_recorder=trace_recorder,
    )


def build_semantic_parse_service(
    model_service: StructuredModelService | None = None,
) -> SemanticParseService:
    """装配候选资产后的语义解析服务。"""

    resolved_model_service = model_service or build_question_model_service(
        enforce_json=True,
    )
    return SemanticParseService(resolved_model_service)



def build_chat_record_service(session: Session) -> ChatRecordService:
    """从 Conversation 组合入口获取 ChatRecord Service。"""

    return build_conversation_chat_record_service(session)


def build_conversation_service(
    session: Session,
) -> ConversationService:
    """从 Conversation 组合入口获取会话 Service。"""

    return build_owned_conversation_service(session)


def build_conversation_reader_service(session: Session) -> ConversationService:
    """装配只需要会话读取与所有权校验的 Service。"""

    return build_conversation_service(session)


def build_history_query_service(session: Session) -> HistoryQueryService:
    """从 Conversation 组合入口获取历史查询 Service。"""

    return build_conversation_history_query_service(session)


def build_chat_log_service(session: Session) -> ChatLogService:
    """从 Conversation 组合入口获取执行日志写入 Service。"""

    return build_conversation_chat_log_service(session)


def build_conversation_history_reader(
    session: Session,
) -> ConversationHistoryReader:
    """装配会话历史富化读取与 data_live 重新执行服务。"""

    return ConversationHistoryReader(
        build_conversation_service(session),
        build_conversation_history_query_service(session),
        build_semantic_dataset_catalog_service(session),
        build_datasource_service(session),
        build_query_service(session),
    )


def resolve_dataset_chat_binding(
    session: Session,
    current_user: object,
    dataset_id: int | None,
) -> ConversationBinding:
    """旧签名兼容：按当前用户工作空间解析数据集执行绑定。"""

    workspace_id = getattr(current_user, "oid", None)
    return resolve_conversation_binding(
        build_semantic_dataset_binding_service(session),
        build_datasource_service(session),
        workspace_id=workspace_id if workspace_id is not None else 1,
        dataset_id=dataset_id,
    )


def build_chat_deletion_service(
    session: Session,
    *,
    agent_cleanup_factory: Callable[[Session], ExecutionCleanupGateway],
) -> ChatDeletionService:
    """装配使用独立模块事务的会话联合删除服务。"""

    def cleanup_session_factory() -> Session:
        return Session(engine)

    return ChatDeletionService(
        build_conversation_service(session),
        agent_cleanup=CommittedAgentCleanupGateway(
            cleanup_session_factory,
            agent_cleanup_factory,
        ),
        artifact_cleanup=WorkflowArtifactCleanupGateway(
            cleanup_session_factory,
            build_result_artifact_service,
        ),
    )


AgentCleanupFactory = Callable[[Session], ExecutionCleanupGateway]

_agent_cleanup_factory: AgentCleanupFactory | None = None


def configure_agent_cleanup(factory: AgentCleanupFactory) -> None:
    """注册会话联合删除所需的 Agent 执行数据清理能力。"""

    global _agent_cleanup_factory
    _agent_cleanup_factory = factory


def build_chat_application_service(session: Session) -> ChatApplicationService:
    """装配 ChatBI 会话创建与联合删除编排。"""

    if _agent_cleanup_factory is None:
        raise RuntimeError("ChatBI Agent cleanup factory is not configured")

    def resolve_binding(current_user: object, dataset_id: int) -> ConversationBinding:
        return resolve_dataset_chat_binding(session, current_user, dataset_id)

    def list_recommended_questions(datasource_id: int) -> list[str] | None:
        return build_recommended_problem_service(session).list_for_chat(datasource_id)

    return ChatApplicationService(
        conversation_service=build_conversation_service(session),
        resolve_binding=resolve_binding,
        list_recommended_questions=list_recommended_questions,
        deletion_service=build_chat_deletion_service(
            session,
            agent_cleanup_factory=_agent_cleanup_factory,
        ),
    )


__all__ = [
    "build_agent_event_publisher",
    "build_agent_trace_recorder",
    "build_chat_application_service",
    "build_chat_deletion_service",
    "build_chat_log_service",
    "build_chat_record_service",
    "build_conversation_history_reader",
    "build_conversation_reader_service",
    "build_conversation_service",
    "build_datasource_selection_candidate_service",
    "build_generation_context_service",
    "build_generation_schema_context_service",
    "build_history_query_service",
    "build_physical_schema_service",
    "build_query_service",
    "build_result_artifact_service",
    "build_question_understanding_service",
    "configure_agent_cleanup",
    "resolve_dataset_chat_binding",
]
