from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from apps.chat.models.chat_model import ChatRecord
from apps.chatbi_agent import crud
from apps.chatbi_agent.loop import AgentLoop
from apps.chatbi_agent.models import AgentClarificationStatus, AgentRunStatus
from apps.chatbi_agent.schemas import (
    AgentClarificationRequest,
    AgentConfig,
    AgentQuestionRequest,
    AgentResumeStreamRequest,
    AgentStartStreamRequest,
    AgentStreamRequest,
)
from common.core.config import settings
from common.core.db import engine
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Agent Data Q&A"], prefix="/chat/agent")


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


@router.post("/stream")
async def agent_stream(current_user: CurrentUser, request: AgentStreamRequest):
    """Agent 唯一实时入口；start 与 resume 共享同一套 SSE 事件协议。"""

    config = get_agent_config()
    if not config.enabled:
        raise HTTPException(status_code=400, detail="Agent ChatBI is not enabled")

    if isinstance(request, AgentStartStreamRequest):
        if (
            request.datasource_id
            and config.datasource_allowlist
            and request.datasource_id not in config.datasource_allowlist
        ):
            raise HTTPException(status_code=400, detail="Datasource is not enabled for Agent ChatBI")

        def stream_start():
            # SSE 流由 generator 自己持有 session，避免跨生命周期传递 ORM 对象。
            with Session(engine) as stream_session:
                record, run = crud.create_record_and_run(
                    stream_session,
                    current_user,
                    AgentQuestionRequest(**request.model_dump(exclude={"action"})),
                    config.model_dump(),
                )
                loop = AgentLoop(stream_session, current_user, config)
                yield from loop.run(run, record)

        return StreamingResponse(stream_start(), media_type="text/event-stream")

    answer = request.clarification
    answer_text = _clarification_answer_text(answer)
    if not answer_text:
        raise HTTPException(status_code=400, detail="Clarification answer is empty")

    def stream_resume():
        # resume 复用同一入口和事件格式，但会开启新的 HTTP 响应流。
        with Session(engine) as stream_session:
            record = stream_session.get(ChatRecord, request.record_id)
            if not record or record.create_by != current_user.id:
                raise RuntimeError("Chat record not found")
            run = crud.get_latest_run_by_record(stream_session, request.record_id)
            clarification = crud.get_pending_clarification(stream_session, request.record_id)
            if not run or not clarification or run.status != AgentRunStatus.WAITING_USER.value:
                raise RuntimeError("No pending clarification")
            clarification.status = AgentClarificationStatus.ANSWERED.value
            clarification.answer = {"selections": answer.selections, "text": answer.text}
            clarification.answered_at = crud.now()
            stream_session.add(clarification)
            stream_session.commit()
            loop = AgentLoop(stream_session, current_user, config)
            yield from loop.resume(run, record, clarification, answer_text)

    return StreamingResponse(stream_resume(), media_type="text/event-stream")


@router.post("/question", deprecated=True)
async def agent_question(current_user: CurrentUser, request: AgentQuestionRequest):
    """兼容旧客户端；新接入统一使用 /stream。"""

    return await agent_stream(
        current_user,
        AgentStartStreamRequest(action="start", **request.model_dump()),
    )


@router.post("/record/{record_id}/clarification", deprecated=True)
async def agent_clarification(
    current_user: CurrentUser,
    record_id: int,
    request: AgentClarificationRequest,
):
    """兼容旧客户端；新接入统一使用 /stream。"""

    return await agent_stream(
        current_user,
        AgentResumeStreamRequest(
            action="resume",
            record_id=record_id,
            clarification=request,
        ),
    )


def _clarification_answer_text(request: AgentClarificationRequest) -> str:
    parts = []
    for item in request.selections:
        label = item.get("label") or item.get("value")
        if label:
            parts.append(str(label))
    if request.text and request.text.strip():
        parts.append(request.text.strip())
    return "用户澄清回答：" + "；".join(parts) if parts else ""


@router.get("/record/{record_id}/trace")
async def agent_trace(session: SessionDep, current_user: CurrentUser, record_id: int):
    record = session.get(ChatRecord, record_id)
    if not record or record.create_by != current_user.id:
        raise HTTPException(status_code=404, detail="Chat record not found")
    return crud.build_trace_response(session, record_id)


@router.get("/runs/{run_id}/events")
async def agent_events(session: SessionDep, current_user: CurrentUser, run_id: int, after_sequence: int = 0):
    run = crud.get_run(session, run_id)
    if not run or run.created_by != current_user.id:
        raise HTTPException(status_code=404, detail="Run not found")
    events = crud.list_events_after(session, run_id, after_sequence)
    return {
        "run_id": run_id,
        "status": run.status,
        "events": [
            {"sequence": event.sequence, "type": event.event_type, **(event.payload or {})} for event in events
        ],
    }


@router.post("/runs/{run_id}/cancel")
async def agent_cancel(session: SessionDep, current_user: CurrentUser, run_id: int):
    run = crud.get_run(session, run_id)
    if not run or run.created_by != current_user.id:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status in {AgentRunStatus.FINISHED.value, AgentRunStatus.FAILED.value, AgentRunStatus.CANCELLED.value}:
        return {"run_id": run_id, "status": run.status}
    crud.update_run(session, run, status=AgentRunStatus.CANCELLED.value)
    record = session.get(ChatRecord, run.record_id)
    if record:
        crud.finish_record(session, record, AgentRunStatus.CANCELLED.value)
    session.commit()
    return {"run_id": run_id, "status": AgentRunStatus.CANCELLED.value}
