from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from common.core.deps import CurrentUser, SessionDep
from sqlbot_platform.workflow_engine.api.schemas import (
    ControlResponse,
    GraphChatQueryRequest,
    GraphEventListResponse,
    GraphQueryRequest,
    GraphRunResponse,
    GraphTraceResponse,
    InteractionResponseRequest,
)
from sqlbot_platform.workflow_engine.api.service import GraphApiService

router = APIRouter(tags=["Graph Workflow"], prefix="/graph")


@router.post("/queries", response_model=GraphRunResponse)
def create_query(session: SessionDep, current_user: CurrentUser, request: GraphQueryRequest):
    return GraphApiService(session).create_query(current_user, request)


@router.post("/queries/stream")
def stream_query(session: SessionDep, current_user: CurrentUser, request: GraphQueryRequest):
    return StreamingResponse(
        GraphApiService(session).stream_query(current_user, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chats/{chat_id}/queries", response_model=GraphRunResponse)
def create_chat_query(
    session: SessionDep,
    current_user: CurrentUser,
    chat_id: int,
    request: GraphChatQueryRequest,
):
    return GraphApiService(session).create_chat_query(current_user, chat_id, request)


@router.post("/chats/{chat_id}/queries/stream")
def stream_chat_query(
    session: SessionDep,
    current_user: CurrentUser,
    chat_id: int,
    request: GraphChatQueryRequest,
):
    return StreamingResponse(
        GraphApiService(session).stream_chat_query(current_user, chat_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}", response_model=GraphRunResponse)
def get_run(session: SessionDep, current_user: CurrentUser, run_id: str):
    return GraphApiService(session).get_run(current_user, run_id)


@router.get("/runs/{run_id}/events", response_model=GraphEventListResponse)
def list_events(
    session: SessionDep,
    current_user: CurrentUser,
    run_id: str,
    after_sequence: int = Query(default=0, ge=0),
):
    return GraphApiService(session).list_events(current_user, run_id, after_sequence=after_sequence)


@router.get("/runs/{run_id}/events/stream")
def stream_events(
    session: SessionDep,
    current_user: CurrentUser,
    run_id: str,
    after_sequence: int = Query(default=0, ge=0),
):
    return StreamingResponse(
        GraphApiService(session).stream_events(current_user, run_id, after_sequence=after_sequence),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/trace", response_model=GraphTraceResponse)
def get_trace(session: SessionDep, current_user: CurrentUser, run_id: str):
    return GraphApiService(session).get_trace(current_user, run_id)


@router.post("/runs/{run_id}/interactions/{interaction_id}/responses", response_model=ControlResponse)
def answer_interaction(
    session: SessionDep,
    current_user: CurrentUser,
    run_id: str,
    interaction_id: str,
    request: InteractionResponseRequest,
):
    return GraphApiService(session).answer_interaction(current_user, run_id, interaction_id, request)


@router.post("/runs/{run_id}/interactions/{interaction_id}/responses/stream")
def stream_interaction_response(
    session: SessionDep,
    current_user: CurrentUser,
    run_id: str,
    interaction_id: str,
    request: InteractionResponseRequest,
    after_sequence: int = Query(default=0, ge=0),
):
    return StreamingResponse(
        GraphApiService(session).stream_interaction_response(
            current_user,
            run_id,
            interaction_id,
            request,
            after_sequence=after_sequence,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel", response_model=ControlResponse)
def cancel_run(session: SessionDep, current_user: CurrentUser, run_id: str):
    return GraphApiService(session).cancel(current_user, run_id)


@router.post("/runs/{run_id}/retry", response_model=ControlResponse)
def retry_run(session: SessionDep, current_user: CurrentUser, run_id: str):
    return GraphApiService(session).retry(current_user, run_id)
