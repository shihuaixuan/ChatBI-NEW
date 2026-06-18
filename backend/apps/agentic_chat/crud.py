from datetime import datetime, timedelta

from sqlalchemy import and_, desc, select

from apps.agentic_chat.models import (
    AgenticClarification,
    AgenticClarificationStatus,
    AgenticRun,
    AgenticRunStatus,
    AgenticStep,
    AgenticStepStatus,
    AgenticTraceEvent,
)
from apps.agentic_chat.schemas import (
    AgenticQuestionRequest,
    AgenticStepInfo,
    AgenticTraceResponse,
    AgenticUnderstandingSummary,
)
from apps.chat.models.chat_model import Chat, ChatRecord
from common.core.deps import CurrentUser, SessionDep


def now() -> datetime:
    return datetime.now()


def get_chat_for_user(session: SessionDep, chat_id: int, current_user: CurrentUser) -> Chat:
    chat = session.get(Chat, chat_id)
    if not chat or chat.create_by != current_user.id:
        raise ValueError(f"Chat with id {chat_id} not found")
    return chat


def create_record_and_run(
    session: SessionDep,
    current_user: CurrentUser,
    request: AgenticQuestionRequest,
    config: dict,
) -> tuple[ChatRecord, AgenticRun]:
    chat = get_chat_for_user(session, request.chat_id, current_user)
    datasource_id = request.datasource_id or chat.datasource
    created_at = now()
    record = ChatRecord(
        chat_id=request.chat_id,
        create_time=created_at,
        create_by=current_user.id,
        datasource=datasource_id,
        engine_type=chat.engine_type,
        question=request.question,
        finish=False,
        status=AgenticRunStatus.CREATED.value,
    )
    session.add(record)
    session.flush()
    session.refresh(record)

    run = AgenticRun(
        oid=current_user.oid if current_user.oid is not None else 1,
        chat_id=request.chat_id,
        record_id=record.id,
        status=AgenticRunStatus.CREATED.value,
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


def start_step(session: SessionDep, run: AgenticRun, step_index: int, action: str, tool_name: str | None) -> AgenticStep:
    step = AgenticStep(
        run_id=run.id,
        step_index=step_index,
        step_type=action,
        tool_name=tool_name,
        status=AgenticStepStatus.RUNNING.value,
        created_at=now(),
    )
    session.add(step)
    session.flush()
    session.refresh(step)
    return step


def finish_step(session: SessionDep, step: AgenticStep, output_summary: dict | None = None) -> None:
    step.status = AgenticStepStatus.SUCCESS.value
    step.output_summary = output_summary or {}
    step.finished_at = now()
    if step.created_at:
        step.duration_ms = int((step.finished_at - step.created_at).total_seconds() * 1000)
    session.add(step)


def fail_step(session: SessionDep, step: AgenticStep, error: str) -> None:
    step.status = AgenticStepStatus.FAILED.value
    step.error = error
    step.finished_at = now()
    if step.created_at:
        step.duration_ms = int((step.finished_at - step.created_at).total_seconds() * 1000)
    session.add(step)


def update_run_state(session: SessionDep, run: AgenticRun, status: str, current_step: str | None, state: dict) -> None:
    run.status = status
    run.current_step = current_step
    run.state = state
    run.updated_at = now()
    session.add(run)


def finish_record(session: SessionDep, record: ChatRecord, status: str, error: str | None = None) -> None:
    record.status = status
    record.finish = status == AgenticRunStatus.FINISHED.value
    record.error = error
    record.finish_time = now()
    session.add(record)


def append_trace(
    session: SessionDep,
    run_id: int,
    event_type: str,
    public_payload: dict,
    step_id: int | None = None,
    private_payload: dict | None = None,
) -> AgenticTraceEvent:
    event = AgenticTraceEvent(
        run_id=run_id,
        step_id=step_id,
        event_type=event_type,
        public_payload=public_payload,
        private_payload=private_payload or {},
        created_at=now(),
    )
    session.add(event)
    session.flush()
    return event


def create_clarification(
    session: SessionDep,
    run: AgenticRun,
    target_slots: list[str],
    question: str,
    user_id: int | None,
    options: list[dict] | None = None,
) -> AgenticClarification:
    clarification = AgenticClarification(
        oid=run.oid,
        run_id=run.id,
        record_id=run.record_id,
        target_slots=target_slots,
        question=question,
        options=options or [],
        created_at=now(),
        expires_at=now() + timedelta(hours=24),
        created_by=user_id,
    )
    session.add(clarification)
    session.flush()
    session.refresh(clarification)
    return clarification


def get_latest_run_by_record(session: SessionDep, record_id: int) -> AgenticRun | None:
    stmt = select(AgenticRun).where(AgenticRun.record_id == record_id).order_by(desc(AgenticRun.created_at)).limit(1)
    return session.exec(stmt).scalars().first()


def get_pending_clarification(session: SessionDep, record_id: int) -> AgenticClarification | None:
    stmt = (
        select(AgenticClarification)
        .where(
            and_(
                AgenticClarification.record_id == record_id,
                AgenticClarification.status == AgenticClarificationStatus.PENDING.value,
            )
        )
        .order_by(desc(AgenticClarification.created_at))
        .limit(1)
    )
    return session.exec(stmt).scalars().first()


def build_trace_response(session: SessionDep, record_id: int) -> AgenticTraceResponse:
    run = get_latest_run_by_record(session, record_id)
    if not run:
        return AgenticTraceResponse(record_id=record_id)
    steps = session.exec(select(AgenticStep).where(AgenticStep.run_id == run.id).order_by(AgenticStep.step_index)).scalars().all()
    events = session.exec(
        select(AgenticTraceEvent).where(AgenticTraceEvent.run_id == run.id).order_by(AgenticTraceEvent.created_at)
    ).scalars().all()
    clarification = get_pending_clarification(session, record_id)
    return build_trace_response_from_objects(record_id, run, steps, events, clarification)


def build_trace_response_from_objects(
    record_id: int,
    run: AgenticRun,
    steps: list[AgenticStep],
    events: list[AgenticTraceEvent],
    clarification: AgenticClarification | None = None,
) -> AgenticTraceResponse:
    # trace API 只拼 public payload；private_payload 不进入普通响应。
    clarification_payload = None
    if clarification:
        clarification_payload = {
            "clarification_id": clarification.id,
            "target_slots": clarification.target_slots or [],
            "question": clarification.question,
            "status": clarification.status,
            "options": clarification.options or [],
        }
    step_infos = []
    for step in steps:
        summary = step.output_summary or {}
        understanding = None
        if step.step_type == "understand_query":
            understanding = AgenticUnderstandingSummary(
                normalized_question=summary.get("normalized_question"),
                intent=summary.get("intent"),
                intent_confidence=summary.get("intent_confidence"),
                slots=summary.get("slots") or {},
                missing_slots=summary.get("missing_slots") or [],
                low_confidence_slots=summary.get("low_confidence_slots") or [],
                ambiguous_slots=summary.get("ambiguous_slots") or [],
                conflict_slots=summary.get("conflict_slots") or [],
            )
        step_infos.append(
            AgenticStepInfo(
                index=step.step_index,
                action=step.step_type,
                status=step.status,
                tool_name=step.tool_name,
                strategy=step.strategy,
                duration_ms=step.duration_ms,
                summary=summary,
                understanding=understanding,
            )
        )
    return AgenticTraceResponse(
        record_id=record_id,
        run_id=run.id,
        status=run.status,
        steps=step_infos,
        events=[{"type": event.event_type, **(event.public_payload or {})} for event in events],
        clarification=clarification_payload,
    )
