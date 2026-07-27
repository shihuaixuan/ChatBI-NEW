from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from apps.chatbi.models import AgentRunStatus
from apps.chatbi.models.dto.agent import (
    AgentClarificationRequest,
    AgentQuestionRequest,
    AgentResumeStreamRequest,
    AgentStartStreamRequest,
    AgentStreamRequest,
)
from apps.chatbi.orchestration.agent.service import (
    AgentDatasourceNotAllowedError,
    AgentNotEnabledError,
    create_agent_resume_events,
    create_agent_start_events,
)
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.conversation import ChatRecordError, ChatRecordExecutionType, ChatRecordStatus
from apps.conversation.composition import build_chat_record_service
from apps.event import encode_sse_events, list_events_after
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Agent Data Q&A"], prefix="/chat/agent")


@router.post("/stream")
async def agent_stream(current_user: CurrentUser, request: AgentStreamRequest):
    """Agent 唯一实时入口；start 与 resume 共享同一套 SSE 事件协议。"""

    if isinstance(request, AgentStartStreamRequest):
        try:
            events = create_agent_start_events(current_user, request)
        except (AgentNotEnabledError, AgentDatasourceNotAllowedError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StreamingResponse(encode_sse_events(events), media_type="text/event-stream")

    answer = request.clarification
    answer_text = _clarification_answer_text(answer)
    if not answer_text:
        raise HTTPException(status_code=400, detail="Clarification answer is empty")

    try:
        events = create_agent_resume_events(current_user, request, answer_text)
    except AgentNotEnabledError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StreamingResponse(encode_sse_events(events), media_type="text/event-stream")


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


def _agent_timeline(session: SessionDep, current_user: CurrentUser, record_id: int):
    """校验记录归属并返回产品 Timeline。"""

    try:
        build_chat_record_service(session).get_owned(current_user.id, record_id)
    except ChatRecordError:
        raise HTTPException(status_code=404, detail="Chat record not found")
    return agent_run_repository.build_timeline_response(session, record_id)


@router.get("/record/{record_id}/timeline")
async def agent_timeline(session: SessionDep, current_user: CurrentUser, record_id: int):
    return _agent_timeline(session, current_user, record_id)


@router.get("/record/{record_id}/trace", deprecated=True)
async def agent_trace(session: SessionDep, current_user: CurrentUser, record_id: int):
    """兼容旧客户端；返回内容与 Timeline 完全一致。"""

    return _agent_timeline(session, current_user, record_id)


@router.get("/runs/{run_id}/events")
async def agent_events(session: SessionDep, current_user: CurrentUser, run_id: int, after_sequence: int = 0):
    run = agent_run_repository.get_run(session, run_id)
    if not run or run.created_by != current_user.id:
        raise HTTPException(status_code=404, detail="Run not found")
    events = list_events_after(session, run_id, after_sequence)
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
