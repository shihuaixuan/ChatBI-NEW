from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from apps.chatbi.models import (
    AgentClarificationStatus,
    AgentRunStatus,
)
from apps.chatbi.models.dto.agent import (
    AgentClarificationRequest,
    AgentQuestionRequest,
    AgentResumeStreamRequest,
    AgentStartStreamRequest,
    AgentStreamRequest,
)
from apps.chatbi.orchestration.agent.loop import AgentLoop
from apps.chatbi.orchestration.agent.service import (
    AgentDatasourceNotAllowedError,
    AgentNotEnabledError,
    create_agent_start_stream,
    get_agent_config,
)
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.conversation import (
    ChatRecordError,
    ChatRecordExecutionType,
    ChatRecordStatus,
)
from apps.conversation.composition import build_chat_record_service
from common.core.db import engine
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Agent Data Q&A"], prefix="/chat/agent")


@router.post("/stream")
async def agent_stream(current_user: CurrentUser, request: AgentStreamRequest):
    """Agent 唯一实时入口；start 与 resume 共享同一套 SSE 事件协议。"""

    if isinstance(request, AgentStartStreamRequest):
        try:
            stream = create_agent_start_stream(current_user, request)
        except (AgentNotEnabledError, AgentDatasourceNotAllowedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StreamingResponse(stream, media_type="text/event-stream")

    config = get_agent_config()
    if not config.enabled:
        raise HTTPException(status_code=400, detail="Agent ChatBI is not enabled")
    answer = request.clarification
    answer_text = _clarification_answer_text(answer)
    if not answer_text:
        raise HTTPException(status_code=400, detail="Clarification answer is empty")

    def stream_resume():
        # resume 复用同一入口和事件格式，但会开启新的 HTTP 响应流。
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
            if not run or not clarification or run.status != AgentRunStatus.WAITING_USER.value:
                raise RuntimeError("No pending clarification")
            clarification.status = AgentClarificationStatus.ANSWERED.value
            clarification.answer = {"selections": answer.selections, "text": answer.text}
            clarification.answered_at = agent_run_repository.now()
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
    try:
        build_chat_record_service(session).get_owned(current_user.id, record_id)
    except ChatRecordError:
        raise HTTPException(status_code=404, detail="Chat record not found")

    return agent_run_repository.build_trace_response(session, record_id)


@router.get("/runs/{run_id}/events")
async def agent_events(session: SessionDep, current_user: CurrentUser, run_id: int, after_sequence: int = 0):
    run = agent_run_repository.get_run(session, run_id)
    if not run or run.created_by != current_user.id:
        raise HTTPException(status_code=404, detail="Run not found")
    events = agent_run_repository.list_events_after(session, run_id, after_sequence)
    return {
        "run_id": run_id,
        "status": run.status,
        "events": [
            {"sequence": event.sequence, "type": event.event_type, **(event.payload or {})} for event in events
        ],
    }


@router.post("/runs/{run_id}/cancel")
async def agent_cancel(session: SessionDep, current_user: CurrentUser, run_id: int):
    run = agent_run_repository.get_run(session, run_id)
    if not run or run.created_by != current_user.id:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status in {AgentRunStatus.FINISHED.value, AgentRunStatus.FAILED.value, AgentRunStatus.CANCELLED.value}:
        return {"run_id": run_id, "status": run.status}
    agent_run_repository.update_run(
        session,
        run,
        status=AgentRunStatus.CANCELLED.value,
    )
    record_service = build_chat_record_service(session)
    record = record_service.get_owned(current_user.id, run.record_id)
    record_service.transition(
        record,
        ChatRecordStatus.CANCELLED,
        execution_type=ChatRecordExecutionType.AGENT,
    )
    session.commit()
    return {"run_id": run_id, "status": AgentRunStatus.CANCELLED.value}
