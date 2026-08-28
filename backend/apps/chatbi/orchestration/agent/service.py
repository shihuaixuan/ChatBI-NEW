from collections.abc import Iterator
from datetime import datetime
from typing import Any

from sqlmodel import Session

from apps.chatbi.composition import (
    build_agent_trace_recorder,
    build_chat_record_service,
    build_conversation_reader_service,
)
from apps.chatbi.models import (
    AgentClarificationStatus,
    AgentRunStatus,
    ChatbiAgentRun,
    ExecutionBindingData,
)
from apps.chatbi.models.dto.agent import (
    AgentConfig,
    AgentQuestionRequest,
    AgentResumeStreamRequest,
    AgentStartStreamRequest,
)
from apps.chatbi.orchestration.agent.cancellation import (
    DatabaseRunCancellationSignal,
)
from apps.chatbi.orchestration.agent.composition import build_run_orchestrator
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.planning import resolve_execution_binding
from apps.conversation import (
    ChatRecordCreateData,
    ChatRecordError,
    ChatRecordExecutionType,
    ChatRecordStatus,
)
from apps.event import RenderEvent
from apps.semantic.composition import build_semantic_dataset_catalog_service
from apps.temporal import build_dataset_temporal_context, build_run_temporal_context
from apps.trace import TraceNodeSpec, TraceNodeType, agent_attributes
from common.core.config import settings
from common.core.db import engine


class AgentNotEnabledError(RuntimeError):
    """Agent 问数未启用。"""


class AgentDatasourceNotAllowedError(ValueError):
    """当前数据源不在 Agent 允许范围内。"""


def create_record_and_run(
    session: Any,
    current_user: Any,
    request: AgentQuestionRequest,
    config: dict[str, Any],
) -> tuple[Any, ChatbiAgentRun]:
    """在同一事务内创建问答记录和 Agent 运行记录。"""

    chat = build_conversation_reader_service(session).get_owned(
        current_user.id,
        request.chat_id,
    )
    binding = resolve_execution_binding(
        ExecutionBindingData(
            conversation_dataset_id=chat.dataset_id,
            conversation_datasource_id=chat.datasource,
            requested_datasource_id=request.datasource_id,
        )
    )
    record_service = build_chat_record_service(session)
    record = record_service.create(
        ChatRecordCreateData(
            chat_id=request.chat_id,
            user_id=current_user.id,
            question=request.question,
            dataset_id=binding.dataset_id,
            datasource_id=binding.datasource_id,
            engine_type=chat.engine_type,
            execution_type=ChatRecordExecutionType.AGENT,
        )
    )
    if record.id is None:
        raise RuntimeError("CHAT_RECORD_ID_MISSING")
    workspace_id = current_user.oid if current_user.oid is not None else 1
    if binding.dataset_id is None:
        # 旧聊天没有绑定语义数据集时，保留原有全局时间上下文兼容行为。
        temporal_context = build_run_temporal_context()
    else:
        calendar = build_semantic_dataset_catalog_service(session).get_calendar(
            workspace_id,
            binding.dataset_id,
        )
        if calendar is None:
            raise RuntimeError("SEMANTIC_DATASET_NOT_FOUND")
        temporal_context = build_dataset_temporal_context(
            default_timezone=calendar.default_timezone,
            calendar_type=calendar.calendar_type,
            week_start_day=calendar.week_start_day,
            fiscal_year_start_month=calendar.fiscal_year_start_month,
            holiday_calendar_key=calendar.holiday_calendar_key,
        )
    run = agent_run_repository.create_run(
        session,
        oid=workspace_id,
        chat_id=request.chat_id,
        record_id=record.id,
        user_id=current_user.id,
        config=config,
        temporal_context=temporal_context,
    )
    record_service.transition(
        record,
        ChatRecordStatus.CREATED,
        run_id=str(run.id),
        execution_type=ChatRecordExecutionType.AGENT,
    )
    session.commit()
    session.refresh(record)
    session.refresh(run)
    return record, run


def get_agent_config() -> AgentConfig:
    allowlist = [
        int(item.strip())
        for item in (settings.CHAT_AGENT_DATASOURCE_ALLOWLIST or "").split(",")
        if item.strip().isdigit()
    ]
    return AgentConfig(
        enabled=settings.CHAT_AGENT_ENABLED,
        datasource_allowlist=allowlist,
        max_steps=settings.CHAT_AGENT_MAX_STEPS,
        max_sql_retries=settings.CHAT_AGENT_MAX_SQL_RETRIES,
        max_clarifications=settings.CHAT_AGENT_MAX_CLARIFICATIONS,
        timeout_seconds=settings.CHAT_AGENT_TIMEOUT_SECONDS,
        token_budget=settings.CHAT_AGENT_TOKEN_BUDGET,
        default_limit=settings.CHAT_AGENT_DEFAULT_LIMIT,
        history_rounds=settings.CHAT_AGENT_HISTORY_ROUNDS,
        context_fold_chars=settings.CHAT_AGENT_CONTEXT_FOLD_CHARS,
        execution_modes=tuple(
            item.strip().lower()
            for item in (
                settings.CHAT_AGENT_EXECUTION_MODES or "fast,plan,research"
            ).split(",")
            if item.strip()
        ),
        plan_max_query_tasks=settings.CHATBI_PLAN_MAX_QUERY_TASKS,
        plan_query_concurrency=settings.CHATBI_PLAN_QUERY_CONCURRENCY,
        research_max_iterations=settings.CHAT_AGENT_RESEARCH_MAX_ITERATIONS,
        research_max_queries=settings.CHAT_AGENT_RESEARCH_MAX_QUERIES,
        research_max_model_calls=settings.CHAT_AGENT_RESEARCH_MAX_MODEL_CALLS,
        research_max_duration_seconds=(
            settings.CHAT_AGENT_RESEARCH_MAX_DURATION_SECONDS
        ),
        research_max_evidence_rows=settings.CHAT_AGENT_RESEARCH_MAX_EVIDENCE_ROWS,
        research_max_evidence_chars=settings.CHAT_AGENT_RESEARCH_MAX_EVIDENCE_CHARS,
        compute_enabled=settings.CHATBI_COMPUTE_ENABLED,
        answer_citation_enforced=settings.CHATBI_ANSWER_CITATION_ENFORCED,
        assisted_fallback_enabled=settings.CHATBI_ASSISTED_FALLBACK_ENABLED,
    )


def create_agent_start_events(
    current_user: Any,
    request: AgentStartStreamRequest,
) -> Iterator[RenderEvent]:
    """创建 Agent 首次提问的结构化事件流。"""

    request_started_at = datetime.now()
    config = get_agent_config()
    if not config.enabled:
        raise AgentNotEnabledError("Agent ChatBI is not enabled")
    if (request.datasource_id and config.datasource_allowlist and request.datasource_id not in config.datasource_allowlist):
        raise AgentDatasourceNotAllowedError("Datasource is not enabled for Agent ChatBI")

    def stream() -> Iterator[RenderEvent]:
        # 事件迭代器自己持有 session，事件生成后立即通过 SSE 推送。
        # 1. 数据库会话与资源创建
        with Session(engine) as stream_session:
            record, run = create_record_and_run(
                stream_session,
                current_user,
                AgentQuestionRequest(**request.model_dump(exclude={"action"})),
                config.model_dump(),
            )
            # 2. 构建追踪（Trace）记录器
            recorder = build_agent_trace_recorder()
            with recorder.node(
                TraceNodeSpec(
                    run_id=run.id or 0,
                    node_key="request_access:initial",
                    node_type=TraceNodeType.PHASE,
                    name="request_access",
                    display_name="用户输入与请求接入",
                    attributes=agent_attributes(
                        run_id=run.id or 0,
                        record_id=record.id or 0,
                        chat_id=run.chat_id,
                    ),
                ),
                input_data={
                    "chat_id": request.chat_id,
                    "datasource_id": request.datasource_id,
                },
                input_detail={"question": request.question},
                started_at=request_started_at,
            ) as access_node:
                access_node.set_output(
                    {
                        "access_status": "accepted",
                        "record_id": record.id,
                        "run_id": run.id,
                        "conversation_owned": True,
                        "datasource_allowed": True,
                    }
                )
            # 3. 构建 Orchestrator 并执行 Agent 运行
            orchestrator = build_run_orchestrator(
                stream_session,
                current_user,
                config,
                recorder=recorder,
                cancellation_signal_factory=lambda run_id: (
                    DatabaseRunCancellationSignal(engine, run_id)
                ),
            )
            yield from orchestrator.run(run, record)

    return stream()


def create_agent_resume_events(
    current_user: Any,
    request: AgentResumeStreamRequest,
    answer_text: str,
) -> Iterator[RenderEvent]:
    """校验挂起状态并创建 Agent 恢复执行的结构化事件流。"""

    request_started_at = datetime.now()
    config = get_agent_config()
    if not config.enabled:
        raise AgentNotEnabledError("Agent ChatBI is not enabled")

    def stream() -> Iterator[RenderEvent]:
        with Session(engine) as stream_session:
            try:
                record = build_chat_record_service(stream_session).get_owned(
                    current_user.id,
                    request.record_id,
                )
            except ChatRecordError as exc:
                raise RuntimeError("Chat record not found") from exc
            run = agent_run_repository.get_latest_run_by_record(
                stream_session,
                request.record_id,
            )
            clarification = agent_run_repository.get_pending_clarification(
                stream_session,
                request.record_id,
            )
            if (
                not run
                or not clarification
                or run.status != AgentRunStatus.WAITING_USER.value
            ):
                raise RuntimeError("No pending clarification")
            clarification.status = AgentClarificationStatus.ANSWERED.value
            clarification.answer = {
                "selections": request.clarification.selections,
                "text": request.clarification.text,
            }
            clarification.answered_at = agent_run_repository.now()
            stream_session.add(clarification)
            stream_session.commit()
            recorder = build_agent_trace_recorder()
            with recorder.node(
                TraceNodeSpec(
                    run_id=run.id or 0,
                    node_key=f"request_access:resume:{clarification.id}",
                    node_type=TraceNodeType.PHASE,
                    name="request_access",
                    display_name="澄清回复接入",
                    attributes=agent_attributes(
                        run_id=run.id or 0,
                        record_id=record.id or 0,
                        chat_id=run.chat_id,
                    ),
                    metadata={"clarification_id": clarification.id},
                ),
                input_data={
                    "record_id": request.record_id,
                    "clarification_id": clarification.id,
                },
                input_detail={"answer_text": answer_text},
                started_at=request_started_at,
            ) as access_node:
                access_node.set_output(
                    {
                        "access_status": "accepted",
                        "run_status": run.status,
                        "clarification_status": clarification.status,
                    }
                )
            orchestrator = build_run_orchestrator(
                stream_session,
                current_user,
                config,
                recorder=recorder,
                cancellation_signal_factory=lambda run_id: (
                    DatabaseRunCancellationSignal(engine, run_id)
                ),
            )
            yield from orchestrator.resume(run, record, clarification, answer_text)

    return stream()
