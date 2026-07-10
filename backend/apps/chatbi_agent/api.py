from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from apps.chat.models.chat_model import ChatRecord
from apps.chatbi_agent import crud
from apps.chatbi_agent.loop import AgentLoop
from apps.chatbi_agent.models import AgentRunStatus
from apps.chatbi_agent.schemas import AgentConfig, AgentQuestionRequest
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
        timeout_seconds=settings.CHAT_AGENT_TIMEOUT_SECONDS,
        token_budget=settings.CHAT_AGENT_TOKEN_BUDGET,
        default_limit=settings.CHAT_AGENT_DEFAULT_LIMIT,
    )


@router.post("/question")
async def agent_question(current_user: CurrentUser, request: AgentQuestionRequest):
    try:
        config = get_agent_config()
        if not config.enabled:
            raise HTTPException(status_code=400, detail="Agent ChatBI is not enabled")
        if request.datasource_id and config.datasource_allowlist and request.datasource_id not in config.datasource_allowlist:
            raise HTTPException(status_code=400, detail="Datasource is not enabled for Agent ChatBI")

        def stream():
            # SSE 流由 generator 自己持有 session，避免跨生命周期传递 ORM 对象。
            with Session(engine) as stream_session:
                record, run = crud.create_record_and_run(
                    stream_session, current_user, request, config.model_dump()
                )
                loop = AgentLoop(stream_session, current_user, config)
                yield from loop.run(run, record)

        return StreamingResponse(stream(), media_type="text/event-stream")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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
