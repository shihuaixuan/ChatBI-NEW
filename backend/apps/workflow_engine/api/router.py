from fastapi import APIRouter, Query

from apps.workflow_engine.api.schemas import (
    ControlResponse,
    GraphEventListResponse,
    GraphQueryRequest,
    GraphRunResponse,
    InteractionResponseRequest,
)
from apps.workflow_engine.api.service import GraphApiService
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Graph Workflow"], prefix="/graph")


@router.post("/queries", response_model=GraphRunResponse)
async def create_query(session: SessionDep, current_user: CurrentUser, request: GraphQueryRequest):
    return GraphApiService(session).create_query(current_user, request)


@router.get("/runs/{run_id}", response_model=GraphRunResponse)
async def get_run(session: SessionDep, current_user: CurrentUser, run_id: str):
    return GraphApiService(session).get_run(current_user, run_id)


@router.get("/runs/{run_id}/events", response_model=GraphEventListResponse)
async def list_events(
    session: SessionDep,
    current_user: CurrentUser,
    run_id: str,
    after_sequence: int = Query(default=0, ge=0),
):
    return GraphApiService(session).list_events(current_user, run_id, after_sequence=after_sequence)


@router.post("/runs/{run_id}/interactions/{interaction_id}/responses", response_model=ControlResponse)
async def answer_interaction(
    session: SessionDep,
    current_user: CurrentUser,
    run_id: str,
    interaction_id: str,
    request: InteractionResponseRequest,
):
    return GraphApiService(session).answer_interaction(current_user, run_id, interaction_id, request)


@router.post("/runs/{run_id}/cancel", response_model=ControlResponse)
async def cancel_run(session: SessionDep, current_user: CurrentUser, run_id: str):
    return GraphApiService(session).cancel(current_user, run_id)


@router.post("/runs/{run_id}/retry", response_model=ControlResponse)
async def retry_run(session: SessionDep, current_user: CurrentUser, run_id: str):
    return GraphApiService(session).retry(current_user, run_id)
