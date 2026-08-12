from datetime import datetime, timedelta

from sqlalchemy import and_, delete, desc, select
from sqlmodel import col

from apps.chatbi.models.orm.agent_run import (
    AgentClarificationStatus,
    AgentRunStatus,
    AgentStepStatus,
    AgentToolCallStatus,
    ChatbiAgentClarification,
    ChatbiAgentRun,
    ChatbiAgentStep,
    ChatbiAgentToolCall,
)
from apps.chatbi.repository.sqlmodel.agent_trace_repository import (
    delete_nodes_for_runs,
)
from apps.conversation.composition import build_chat_record_service
from apps.event import (
    delete_events_for_runs,
)
from apps.event import (
    list_events_after as list_persisted_events_after,
)
from apps.event import (
    next_sequence as next_event_sequence,
)
from apps.temporal import TemporalContext, build_run_temporal_context


def now() -> datetime:
    return datetime.now()


def create_run(
    session,
    *,
    oid: int,
    chat_id: int,
    record_id: int,
    user_id: int,
    config: dict,
    temporal_context: TemporalContext | None = None,
) -> ChatbiAgentRun:
    """创建 Agent 运行记录；事务提交由调用方统一控制。"""

    created_at = now()
    fixed_temporal_context = temporal_context or build_run_temporal_context()
    run = ChatbiAgentRun(
        oid=oid,
        chat_id=chat_id,
        record_id=record_id,
        status=AgentRunStatus.CREATED.value,
        config=config,
        temporal_context=fixed_temporal_context.model_dump(mode="json"),
        created_at=created_at,
        updated_at=created_at,
        created_by=user_id,
    )
    session.add(run)
    session.flush()
    session.refresh(run)
    return run


def start_step(session, run: ChatbiAgentRun, step_index: int) -> ChatbiAgentStep:
    """创建模型推理轮次；单个工具事实由 Tool Call 记录承载。"""

    step = ChatbiAgentStep(
        run_id=run.id,
        step_index=step_index,
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


def start_tool_call(
    session,
    *,
    run_id: int,
    step_id: int,
    tool_call_id: str,
    tool_name: str,
    args_summary: dict,
) -> ChatbiAgentToolCall:
    """创建独立 Tool Call 记录；事务提交由编排层统一控制。"""

    tool_call = ChatbiAgentToolCall(
        run_id=run_id,
        step_id=step_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        status=AgentToolCallStatus.RUNNING.value,
        args_summary=args_summary,
        started_at=now(),
    )
    session.add(tool_call)
    session.flush()
    session.refresh(tool_call)
    return tool_call


def finish_tool_call(
    session,
    tool_call: ChatbiAgentToolCall,
    *,
    status: AgentToolCallStatus,
    result_summary: dict,
    error_code: str | None = None,
) -> None:
    """设置 Tool Call 终态；记录与对应事件必须由调用方在同一事务提交。"""

    if status == AgentToolCallStatus.RUNNING:
        raise ValueError("TOOL_CALL_TERMINAL_STATUS_REQUIRED")
    tool_call.status = status.value
    tool_call.result_summary = result_summary
    tool_call.error_code = error_code
    tool_call.finished_at = now()
    if tool_call.started_at:
        tool_call.latency_ms = int(
            (tool_call.finished_at - tool_call.started_at).total_seconds() * 1000
        )
    session.add(tool_call)


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


next_sequence = next_event_sequence
list_events_after = list_persisted_events_after


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

    records = build_chat_record_service(session).list_recent_completed(
        chat_id=chat_id,
        exclude_record_id=exclude_record_id,
        limit=limit,
    )
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
    ]

    stmt = (
        select(ChatbiAgentRun)
        .where(and_(*conditions))
        .order_by(desc(ChatbiAgentRun.created_at))
        .limit(10)
    )
    record_service = build_chat_record_service(session)
    for previous_run in session.exec(stmt).scalars().all():
        record = record_service.get(previous_run.record_id)
        if not record.finish or record.execution_type != "agent":
            continue
        if datasource_id is not None and record.datasource != datasource_id:
            continue
        understanding = (previous_run.derived_state or {}).get("question_understanding")
        if not isinstance(understanding, dict):
            continue
        rewritten_question = understanding.get("rewritten_question")
        if isinstance(rewritten_question, str) and rewritten_question.strip():
            return rewritten_question.strip()
    return None


def build_timeline_response(session, record_id: int) -> dict:
    """构建产品运行时间线，不读取可观测性 Trace。"""

    run = get_latest_run_by_record(session, record_id)
    if not run:
        return {"record_id": record_id, "run_id": None, "status": None, "steps": [], "events": []}
    steps = session.exec(
        select(ChatbiAgentStep).where(ChatbiAgentStep.run_id == run.id).order_by(ChatbiAgentStep.step_index)
    ).scalars().all()
    tool_calls = session.exec(
        select(ChatbiAgentToolCall)
        .where(ChatbiAgentToolCall.run_id == run.id)
        .order_by(ChatbiAgentToolCall.started_at, ChatbiAgentToolCall.id)
    ).scalars().all()
    events = list_persisted_events_after(session, run.id, 0)
    return {
        "record_id": record_id,
        "run_id": run.id,
        "status": run.status,
        "error_class": run.error_class,
        "budget": run.budget_snapshot or {},
        "steps": [
            {
                "id": step.id,
                "index": step.step_index,
                "status": step.status,
                "latency_ms": step.latency_ms,
                "result_summary": step.result_summary or {},
                "token_usage": step.token_usage,
                "error": step.error,
            }
            for step in steps
        ],
        "tool_calls": [
            {
                "tool_call_id": item.tool_call_id,
                "step_id": item.step_id,
                "tool_name": item.tool_name,
                "status": item.status,
                "latency_ms": item.latency_ms,
                "args_summary": item.args_summary or {},
                "result_summary": item.result_summary or {},
                "error_code": item.error_code,
            }
            for item in tool_calls
        ],
        "events": [
            {"sequence": event.sequence, **(event.payload or {})} for event in events
        ],
    }


class AgentExecutionDeletionService:
    """删除会话时清理同一会话下的 Agent 执行数据。"""

    def __init__(self, session) -> None:
        self._session = session

    def delete_for_chat(self, chat_id: int) -> int:
        run_ids = list(
            self._session.exec(
                select(ChatbiAgentRun.id).where(ChatbiAgentRun.chat_id == chat_id)
            ).scalars()
        )
        if not run_ids:
            return 0

        delete_events_for_runs(self._session, run_ids)
        delete_nodes_for_runs(self._session, run_ids)
        self._session.execute(
            delete(ChatbiAgentClarification).where(
                col(ChatbiAgentClarification.run_id).in_(run_ids)
            )
        )
        self._session.execute(
            delete(ChatbiAgentToolCall).where(
                col(ChatbiAgentToolCall.run_id).in_(run_ids)
            )
        )
        self._session.execute(
            delete(ChatbiAgentStep).where(col(ChatbiAgentStep.run_id).in_(run_ids))
        )
        self._session.execute(
            delete(ChatbiAgentRun).where(col(ChatbiAgentRun.id).in_(run_ids))
        )
        return len(run_ids)
