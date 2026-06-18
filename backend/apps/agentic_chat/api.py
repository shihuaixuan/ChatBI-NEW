from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from apps.agentic_chat import crud
from apps.agentic_chat.models import (
    AgenticClarificationStatus,
    AgenticRunStatus,
)
from apps.agentic_chat.orchestrator import AgenticOrchestrator
from apps.agentic_chat.schemas import (
    AgenticClarificationRequest,
    AgenticConfig,
    AgenticQuestionRequest,
    AgenticTraceResponse,
)
from apps.chat.models.chat_model import ChatRecord
from common.core.config import settings
from common.core.db import engine
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Agentic Data Q&A"], prefix="/chat/agentic")


def get_agentic_config() -> AgenticConfig:
    allowlist = [
        int(item.strip())
        for item in (settings.CHAT_AGENTIC_FLOW_DATASOURCE_ALLOWLIST or "").split(",")
        if item.strip().isdigit()
    ]
    return AgenticConfig(
        enabled=settings.CHAT_AGENTIC_FLOW_ENABLED,
        assistant_enabled=settings.CHAT_AGENTIC_FLOW_ASSISTANT_ENABLED,
        datasource_allowlist=allowlist,
        max_steps=settings.AGENTIC_MAX_STEPS,
        default_limit=settings.CHAT_AGENTIC_FLOW_DEFAULT_LIMIT,
    )


@router.post("/question")
async def agentic_question(current_user: CurrentUser, request: AgenticQuestionRequest):
    try:
        config = get_agentic_config()
        if not config.enabled:
            raise HTTPException(status_code=400, detail="Agentic ChatBI flow is not enabled")
        if request.datasource_id and config.datasource_allowlist and request.datasource_id not in config.datasource_allowlist:
            raise HTTPException(status_code=400, detail="Datasource is not enabled for Agentic ChatBI")

        def stream():
            # SSE 流式请求由 generator 自己持有 session，避免把外层依赖 session 的 ORM 对象跨生命周期传递。
            with Session(engine) as stream_session:
                stream_record, stream_run = crud.create_record_and_run(stream_session, current_user, request, config.model_dump())
                orchestrator = AgenticOrchestrator(stream_session, current_user, config)
                yield from orchestrator.run(stream_run, stream_record)

        return StreamingResponse(stream(), media_type="text/event-stream")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/record/{record_id}/clarification")
async def agentic_clarification(
    current_user: CurrentUser,
    record_id: int,
    request: AgenticClarificationRequest,
):
    try:
        answers = {item.slot: item.value for item in request.answers}
        config = get_agentic_config()

        def stream():
            # SSE 流式请求由 generator 自己持有 session，恢复校验和后续编排使用同一个 session。
            with Session(engine) as stream_session:
                stream_record = stream_session.get(ChatRecord, record_id)
                if not stream_record or stream_record.create_by != current_user.id:
                    raise RuntimeError("Chat record not found")
                stream_run = crud.get_latest_run_by_record(stream_session, record_id)
                clarification = crud.get_pending_clarification(stream_session, record_id)
                if not stream_run or not clarification or stream_run.status != AgenticRunStatus.WAITING_USER.value:
                    raise RuntimeError("No pending clarification")
                clarification.status = AgenticClarificationStatus.ANSWERED.value
                clarification.answer = answers
                clarification.answered_at = crud.now()
                stream_session.add(clarification)
                stream_session.commit()
                orchestrator = AgenticOrchestrator(stream_session, current_user, config)
                yield from orchestrator.resume(stream_run, stream_record, answers)

        return StreamingResponse(stream(), media_type="text/event-stream")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/record/{record_id}/trace", response_model=AgenticTraceResponse)
async def agentic_trace(session: SessionDep, current_user: CurrentUser, record_id: int):
    record = session.get(ChatRecord, record_id)
    if not record or record.create_by != current_user.id:
        raise HTTPException(status_code=404, detail="Chat record not found")
    return crud.build_trace_response(session, record_id)
