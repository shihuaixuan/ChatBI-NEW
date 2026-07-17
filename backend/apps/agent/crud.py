from datetime import datetime, timedelta

from sqlalchemy import and_, desc, func, select

from apps.chat.models.chat_model import Chat, ChatRecord
from apps.agent.models import (
    AgentClarificationStatus,
    AgentRunStatus,
    AgentStepStatus,
    ChatbiAgentClarification,
    ChatbiAgentRun,
    ChatbiAgentStep,
    ChatbiAgentTraceEvent,
)
from apps.agent.schemas import AgentQuestionRequest


def now() -> datetime:
    return datetime.now()


def get_chat_for_user(session, chat_id: int, current_user) -> Chat:
    chat = session.get(Chat, chat_id)
    if not chat or chat.create_by != current_user.id:
        raise ValueError(f"Chat with id {chat_id} not found")
    return chat


def create_record_and_run(
    session,
    current_user,
    request: AgentQuestionRequest,
    config: dict,
) -> tuple[ChatRecord, ChatbiAgentRun]:
    chat = get_chat_for_user(session, request.chat_id, current_user)
    datasource_id = request.datasource_id or chat.datasource
    created_at = now()
    record = ChatRecord(
        chat_id=request.chat_id,
        create_time=created_at,
        create_by=current_user.id,
        datasource=datasource_id,
        engine_type=chat.engine_type,
        execution_type="agent",
        question=request.question,
        finish=False,
        status=AgentRunStatus.CREATED.value,
    )
    session.add(record)
    session.flush()
    session.refresh(record)

    run = ChatbiAgentRun(
        oid=current_user.oid if current_user.oid is not None else 1,
        chat_id=request.chat_id,
        record_id=record.id,
        status=AgentRunStatus.CREATED.value,
        config=config,
        created_at=created_at,
        updated_at=created_at,
        created_by=current_user.id,
    )
    session.add(run)
    session.flush()
    session.refresh(run)
    record.trace_id = str(run.id)
    session.add(record)
    session.commit()
    session.refresh(record)
    session.refresh(run)
    return record, run


def start_step(session, run: ChatbiAgentRun, step_index: int, tool_name: str | None, args_summary: dict) -> ChatbiAgentStep:
    step = ChatbiAgentStep(
        run_id=run.id,
        step_index=step_index,
        tool_name=tool_name,
        args_summary=args_summary,
        status=AgentStepStatus.RUNNING.value,
        created_at=now(),
    )
    session.add(step)
    session.flush()
    session.refresh(step)
    return step


def finish_step(session, step: ChatbiAgentStep, result_summary: dict, token_usage: dict | None = None) -> None:
    step.status = AgentStepStatus.SUCCESS.value
    step.result_summary = result_summary
    step.token_usage = token_usage
    step.finished_at = now()
    if step.created_at:
        step.latency_ms = int((step.finished_at - step.created_at).total_seconds() * 1000)
    session.add(step)


def fail_step(session, step: ChatbiAgentStep, error: str) -> None:
    step.status = AgentStepStatus.FAILED.value
    step.error = error
    step.finished_at = now()
    if step.created_at:
        step.latency_ms = int((step.finished_at - step.created_at).total_seconds() * 1000)
    session.add(step)


def update_run(
    session,
    run: ChatbiAgentRun,
    *,
    status: str | None = None,
    messages: list | None = None,
    budget_snapshot: dict | None = None,
    derived_state: dict | None = None,
    error_class: str | None = None,
    error: str | None = None,
) -> None:
    if status is not None:
        run.status = status
    if messages is not None:
        run.messages = messages
    if budget_snapshot is not None:
        run.budget_snapshot = budget_snapshot
    if derived_state is not None:
        run.derived_state = derived_state
    if error_class is not None:
        run.error_class = error_class
    if error is not None:
        run.error = error
    run.updated_at = now()
    session.add(run)


def finish_record(session, record: ChatRecord, status: str, error: str | None = None) -> None:
    record.status = status
    record.finish = status == AgentRunStatus.FINISHED.value
    record.error = error
    record.finish_time = now()
    session.add(record)


def next_sequence(session, run_id: int) -> int:
    current = session.exec(
        select(func.max(ChatbiAgentTraceEvent.sequence)).where(ChatbiAgentTraceEvent.run_id == run_id)
    ).scalar()
    return (current or 0) + 1


def append_trace(session, run_id: int, event_type: str, payload: dict, step_id: int | None = None) -> ChatbiAgentTraceEvent:
    event = ChatbiAgentTraceEvent(
        run_id=run_id,
        step_id=step_id,
        sequence=next_sequence(session, run_id),
        event_type=event_type,
        payload=payload,
        created_at=now(),
    )
    session.add(event)
    session.flush()
    return event


def list_events_after(session, run_id: int, after_sequence: int = 0) -> list[ChatbiAgentTraceEvent]:
    stmt = (
        select(ChatbiAgentTraceEvent)
        .where(and_(ChatbiAgentTraceEvent.run_id == run_id, ChatbiAgentTraceEvent.sequence > after_sequence))
        .order_by(ChatbiAgentTraceEvent.sequence)
    )
    return session.exec(stmt).scalars().all()


def create_clarification(
    session,
    run: ChatbiAgentRun,
    *,
    question: str,
    options: list[dict],
    tool_call_id: str | None,
    resume_kind: str,
    resume_payload: dict,
    user_id: int | None,
    expire_hours: int = 24,
) -> ChatbiAgentClarification:
    clarification = ChatbiAgentClarification(
        oid=run.oid,
        run_id=run.id,
        record_id=run.record_id,
        question=question,
        options=options,
        tool_call_id=tool_call_id,
        resume_kind=resume_kind,
        resume_payload=resume_payload,
        created_at=now(),
        expires_at=now() + timedelta(hours=expire_hours),
        created_by=user_id,
    )
    session.add(clarification)
    session.flush()
    session.refresh(clarification)
    return clarification


def get_latest_run_by_record(session, record_id: int) -> ChatbiAgentRun | None:
    stmt = select(ChatbiAgentRun).where(ChatbiAgentRun.record_id == record_id).order_by(desc(ChatbiAgentRun.created_at)).limit(1)
    return session.exec(stmt).scalars().first()


def get_run(session, run_id: int) -> ChatbiAgentRun | None:
    return session.get(ChatbiAgentRun, run_id)


def get_pending_clarification(session, record_id: int) -> ChatbiAgentClarification | None:
    stmt = (
        select(ChatbiAgentClarification)
        .where(
            and_(
                ChatbiAgentClarification.record_id == record_id,
                ChatbiAgentClarification.status == AgentClarificationStatus.PENDING.value,
            )
        )
        .order_by(desc(ChatbiAgentClarification.created_at))
        .limit(1)
    )
    return session.exec(stmt).scalars().first()


def recent_qa_summaries(session, chat_id: int, exclude_record_id: int, limit: int = 3) -> list[dict]:
    """最近 K 轮已完成问答的摘要（question + SQL + 概要），供多轮上下文注入。"""

    stmt = (
        select(ChatRecord)
        .where(
            and_(
                ChatRecord.chat_id == chat_id,
                ChatRecord.id != exclude_record_id,
                ChatRecord.finish.is_(True),
            )
        )
        .order_by(desc(ChatRecord.id))
        .limit(limit)
    )
    records = session.exec(stmt).scalars().all()
    summaries = []
    for record in reversed(records):
        summaries.append(
            {
                "question": record.question,
                "sql": record.sql,
                "answer_brief": (record.sql_answer or "")[:200],
            }
        )
    return summaries


def latest_successful_rewritten_question(
    session,
    *,
    chat_id: int,
    exclude_record_id: int,
    datasource_id: int | None,
) -> str | None:
    """读取同会话、同数据源最近一次成功执行后的完整重写问题。"""

    conditions = [
        ChatbiAgentRun.chat_id == chat_id,
        ChatbiAgentRun.record_id != exclude_record_id,
        ChatbiAgentRun.status == AgentRunStatus.FINISHED.value,
        ChatRecord.finish.is_(True),
        ChatRecord.execution_type == "agent",
    ]
    if datasource_id is not None:
        conditions.append(ChatRecord.datasource == datasource_id)

    stmt = (
        select(ChatbiAgentRun)
        .join(ChatRecord, ChatRecord.id == ChatbiAgentRun.record_id)
        .where(and_(*conditions))
        .order_by(
            desc(ChatRecord.create_time),
            desc(ChatRecord.id),
            desc(ChatbiAgentRun.created_at),
        )
        .limit(10)
    )
    for previous_run in session.exec(stmt).scalars().all():
        understanding = (previous_run.derived_state or {}).get("question_understanding")
        if not isinstance(understanding, dict):
            continue
        rewritten_question = understanding.get("rewritten_question")
        if isinstance(rewritten_question, str) and rewritten_question.strip():
            return rewritten_question.strip()
    return None


def build_trace_response(session, record_id: int) -> dict:
    run = get_latest_run_by_record(session, record_id)
    if not run:
        return {"record_id": record_id, "run_id": None, "status": None, "steps": [], "events": []}
    steps = session.exec(
        select(ChatbiAgentStep).where(ChatbiAgentStep.run_id == run.id).order_by(ChatbiAgentStep.step_index)
    ).scalars().all()
    events = list_events_after(session, run.id, 0)
    return {
        "record_id": record_id,
        "run_id": run.id,
        "status": run.status,
        "error_class": run.error_class,
        "budget": run.budget_snapshot or {},
        "steps": [
            {
                "index": step.step_index,
                "tool_name": step.tool_name,
                "status": step.status,
                "latency_ms": step.latency_ms,
                "args_summary": step.args_summary or {},
                "result_summary": step.result_summary or {},
                "token_usage": step.token_usage,
                "error": step.error,
            }
            for step in steps
        ],
        "events": [
            {"sequence": event.sequence, "type": event.event_type, **(event.payload or {})} for event in events
        ],
    }
