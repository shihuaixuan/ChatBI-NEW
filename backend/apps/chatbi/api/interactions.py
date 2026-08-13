from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from apps.chatbi.composition import build_result_artifact_service
from apps.chatbi.errors import ResultArtifactError
from apps.chatbi.models import (
    AgentRunStatus,
    AgentTraceNodeDetailSnapshot,
    AgentTraceSnapshot,
    ChatbiAgentRun,
)
from apps.chatbi.models.dto.agent import (
    AgentClarificationRequest,
    AgentStartStreamRequest,
    AgentStreamRequest,
)
from apps.chatbi.orchestration.agent.service import (
    AgentDatasourceNotAllowedError,
    AgentNotEnabledError,
    create_agent_resume_events,
    create_agent_start_events,
)
from apps.chatbi.repository.sqlmodel import (
    agent_run_repository,
    agent_trace_repository,
)
from apps.chatbi.services.trace_projection import (
    load_agent_trace_node_detail,
    project_agent_trace,
)
from apps.conversation import ChatRecordError, ChatRecordExecutionType, ChatRecordStatus
from apps.conversation.composition import build_chat_record_service
from apps.event import encode_sse_events, list_events_after
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Agent Data Q&A"], prefix="/chat/agent")


@router.post("/stream")
async def agent_stream(
    current_user: CurrentUser,
    request: AgentStreamRequest,
) -> StreamingResponse:
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


def _clarification_answer_text(request: AgentClarificationRequest) -> str:
    parts = []
    for item in request.selections:
        label = item.get("label") or item.get("value")
        if label:
            parts.append(str(label))
    if request.text and request.text.strip():
        parts.append(request.text.strip())
    return "用户澄清回答：" + "；".join(parts) if parts else ""


def _agent_timeline(
    session: SessionDep,
    current_user: CurrentUser,
    record_id: int,
) -> dict[str, Any]:
    """校验记录归属并返回产品 Timeline。"""

    try:
        build_chat_record_service(session).get_owned(current_user.id, record_id)
    except ChatRecordError:
        raise HTTPException(status_code=404, detail="Chat record not found")
    return agent_run_repository.build_timeline_response(session, record_id)


@router.get("/record/{record_id}/timeline")
async def agent_timeline(
    session: SessionDep,
    current_user: CurrentUser,
    record_id: int,
) -> dict[str, Any]:
    return _agent_timeline(session, current_user, record_id)


def _get_owned_agent_run(
    session: SessionDep,
    current_user: CurrentUser,
    record_id: int,
) -> tuple[ChatbiAgentRun, int]:
    """执行详情统一从记录归属进入，不允许仅凭 Run 或 Artifact ID 读取。"""

    try:
        build_chat_record_service(session).get_owned(current_user.id, record_id)
    except ChatRecordError:
        raise HTTPException(status_code=404, detail="Chat record not found")
    run = agent_run_repository.get_latest_run_by_record(session, record_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent trace not found")
    run_id = run.id
    if run_id is None:
        raise HTTPException(status_code=404, detail="Agent trace not found")
    return run, run_id


def _can_view_agent_trace_detail(current_user: CurrentUser) -> bool:
    """首版调试详情仅对系统管理员和工作空间管理员开放。"""

    return bool(
        getattr(current_user, "isAdmin", False)
        or int(getattr(current_user, "weight", 0) or 0) > 0
    )


@router.get(
    "/record/{record_id}/trace",
    response_model=AgentTraceSnapshot,
)
async def agent_trace(
    session: SessionDep,
    current_user: CurrentUser,
    record_id: int,
) -> AgentTraceSnapshot:
    """返回执行概览与调用树，完整输入输出按节点延迟读取。"""

    run, run_id = _get_owned_agent_run(session, current_user, record_id)
    nodes = agent_trace_repository.list_run_nodes(session, run_id)
    return project_agent_trace(
        run,
        nodes,
        detail_access=(
            "allowed"
            if _can_view_agent_trace_detail(current_user)
            else "summary_only"
        ),
    )


@router.get(
    "/record/{record_id}/trace/nodes/{node_id}",
    response_model=AgentTraceNodeDetailSnapshot,
)
async def agent_trace_node_detail(
    session: SessionDep,
    current_user: CurrentUser,
    record_id: int,
    node_id: int,
) -> AgentTraceNodeDetailSnapshot:
    """返回一个已授权节点的摘要和脱敏输入输出正文。"""

    run, run_id = _get_owned_agent_run(session, current_user, record_id)
    if not _can_view_agent_trace_detail(current_user):
        raise HTTPException(status_code=403, detail="Agent trace detail forbidden")
    node = agent_trace_repository.get_run_node(session, run_id, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Agent trace node not found")

    try:
        return load_agent_trace_node_detail(
            run,
            node,
            build_result_artifact_service(session),
        )
    except (ResultArtifactError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Agent trace node detail unavailable",
        ) from exc


@router.get("/runs/{run_id}/events")
async def agent_events(
    session: SessionDep,
    current_user: CurrentUser,
    run_id: int,
    after_sequence: int = 0,
) -> dict[str, Any]:
    run = agent_run_repository.get_run(session, run_id)
    if not run or run.created_by != current_user.id:
        raise HTTPException(status_code=404, detail="Run not found")
    events = list_events_after(session, run_id, after_sequence)
    return {
        "run_id": run_id,
        "status": run.status,
        "events": [
            {"sequence": event.sequence, **(event.payload or {})} for event in events
        ],
    }


@router.post("/runs/{run_id}/cancel")
async def agent_cancel(
    session: SessionDep,
    current_user: CurrentUser,
    run_id: int,
) -> dict[str, Any]:
    run = agent_run_repository.get_run(session, run_id)
    if not run or run.created_by != current_user.id:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status in {
        AgentRunStatus.FINISHED.value,
        AgentRunStatus.FAILED.value,
        AgentRunStatus.CANCELLED.value,
        AgentRunStatus.CANCEL_REQUESTED.value,
    }:
        return {"run_id": run_id, "status": run.status}
    if run.status in {
        AgentRunStatus.CREATED.value,
        AgentRunStatus.WAITING_USER.value,
    }:
        target_status = AgentRunStatus.CANCELLED.value
    else:
        target_status = AgentRunStatus.CANCEL_REQUESTED.value
    agent_run_repository.update_run(
        session,
        run,
        status=target_status,
    )
    if target_status == AgentRunStatus.CANCELLED.value:
        record_service = build_chat_record_service(session)
        record = record_service.get_owned(current_user.id, run.record_id)
        record_service.transition(
            record,
            ChatRecordStatus.CANCELLED,
            execution_type=ChatRecordExecutionType.AGENT,
        )
    session.commit()
    return {"run_id": run_id, "status": target_status}
