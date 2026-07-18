from fastapi import APIRouter
from sqlmodel import Session

from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.models.dto import DimensionPayload
from apps.semantic.models.orm import SemanticDimension
from apps.semantic.repository.sqlmodel.dimension_repository import (
    SqlModelDimensionRepository,
)
from apps.semantic.repository.sqlmodel.model_repository import (
    SqlModelModelRepository,
)
from apps.semantic.services.dimension_service import (
    SemanticDimensionService,
)
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")


def _dimension_service(session: Session) -> SemanticDimensionService:
    return SemanticDimensionService(
        SqlModelDimensionRepository(session),
        SqlModelModelRepository(session),
    )


@router.get("/dimensions")
async def list_dimensions(
    session: SessionDep, current_user: CurrentUser, model_id: int | None = None
) -> list[SemanticDimension]:
    with map_semantic_errors_to_http():
        return _dimension_service(session).list_dimensions(current_user.oid, model_id)


@router.post("/dimensions")
async def create_dimension(
    session: SessionDep, current_user: CurrentUser, payload: DimensionPayload
) -> SemanticDimension:
    with map_semantic_errors_to_http():
        return _dimension_service(session).create_dimension(current_user.oid, payload)


@router.put("/dimensions/{dimension_id}")
async def update_dimension(
    session: SessionDep,
    current_user: CurrentUser,
    dimension_id: int,
    payload: DimensionPayload,
) -> SemanticDimension:
    with map_semantic_errors_to_http():
        return _dimension_service(session).update_dimension(
            current_user.oid, dimension_id, payload
        )


@router.delete("/dimensions/{dimension_id}")
async def delete_dimension(
    session: SessionDep, current_user: CurrentUser, dimension_id: int
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return _dimension_service(session).delete_dimension(
            current_user.oid, dimension_id
        )
