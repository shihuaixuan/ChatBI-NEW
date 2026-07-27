from collections.abc import Iterator

from sqlmodel import Session

from apps.chatbi.composition import (
    build_chat_record_service,
    build_conversation_reader_service,
)
from apps.chatbi.models import (
    AgentClarificationStatus,
    AgentRunStatus,
    ExecutionBindingData,
)
from apps.chatbi.models.dto.agent import (
    AgentConfig,
    AgentQuestionRequest,
    AgentResumeStreamRequest,
    AgentStartStreamRequest,
)
from apps.chatbi.orchestration.agent.composition import build_agent_loop
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.planning import resolve_execution_binding
from apps.conversation import (
    ChatRecordCreateData,
    ChatRecordError,
    ChatRecordExecutionType,
    ChatRecordStatus,
)
from apps.event import RenderEvent
from common.core.config import settings
from common.core.db import engine


class AgentNotEnabledError(RuntimeError):
    """Agent 问数未启用。"""


class AgentDatasourceNotAllowedError(ValueError):
    """当前数据源不在 Agent 允许范围内。"""


def create_record_and_run(
    session,
    current_user,
    request: AgentQuestionRequest,
    config: dict,
):
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
    run = agent_run_repository.create_run(
        session,
        oid=current_user.oid if current_user.oid is not None else 1,
        chat_id=request.chat_id,
        record_id=record.id,
        user_id=current_user.id,
        config=config,
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
    )


def create_agent_start_events(
    current_user,
    request: AgentStartStreamRequest,
) -> Iterator[RenderEvent]:
    """创建 Agent 首次提问的结构化事件流。"""

    config = get_agent_config()
    if not config.enabled:
        raise AgentNotEnabledError("Agent ChatBI is not enabled")
    if (
        request.datasource_id
        and config.datasource_allowlist
        and request.datasource_id not in config.datasource_allowlist
    ):
        raise AgentDatasourceNotAllowedError("Datasource is not enabled for Agent ChatBI")

    def stream() -> Iterator[RenderEvent]:
        # 事件迭代器自己持有 session，避免跨响应生命周期传递 ORM 对象。
        with Session(engine) as stream_session:
            record, run = create_record_and_run(
                stream_session,
                current_user,
                AgentQuestionRequest(**request.model_dump(exclude={"action"})),
                config.model_dump(),
            )
            loop = build_agent_loop(stream_session, current_user, config)
            yield from loop.run(run, record)

    return stream()


def create_agent_resume_events(
    current_user,
    request: AgentResumeStreamRequest,
    answer_text: str,
) -> Iterator[RenderEvent]:
    """校验挂起状态并创建 Agent 恢复执行的结构化事件流。"""

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
            loop = build_agent_loop(stream_session, current_user, config)
            yield from loop.resume(run, record, clarification, answer_text)

    return stream()
