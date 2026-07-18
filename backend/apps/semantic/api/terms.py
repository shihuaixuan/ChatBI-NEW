from fastapi import APIRouter, Query

from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.models.dto import TermPayload
from apps.semantic.models.orm import SemanticTerm
from apps.semantic.repository.sqlmodel.domain_repository import (
    SqlModelDomainRepository,
)
from apps.semantic.repository.sqlmodel.term_repository import SqlModelTermRepository
from apps.semantic.services.term_service import SemanticTermService
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")


@router.get("/terms")
async def list_terms(
    session: SessionDep,
    current_user: CurrentUser,
    domain_id: int | None = None,
    word: str | None = None,
    dataset_ids: list[int] | None = Query(None),
) -> list[SemanticTerm]:
    with map_semantic_errors_to_http():
        return SemanticTermService(
            SqlModelTermRepository(session),
            SqlModelDomainRepository(session),
        ).search_terms(
            current_user.oid,
            domain_id=domain_id,
            word=word,
            dataset_ids=dataset_ids,
        )


@router.post("/terms")
async def create_term(
    session: SessionDep, current_user: CurrentUser, payload: TermPayload
) -> SemanticTerm:
    with map_semantic_errors_to_http():
        return SemanticTermService(
            SqlModelTermRepository(session),
            SqlModelDomainRepository(session),
        ).create_term(
            current_user.oid, payload
        )


@router.put("/terms/{term_id}")
async def update_term(
    session: SessionDep, current_user: CurrentUser, term_id: int, payload: TermPayload
) -> SemanticTerm:
    with map_semantic_errors_to_http():
        return SemanticTermService(
            SqlModelTermRepository(session),
            SqlModelDomainRepository(session),
        ).update_term(
            current_user.oid, term_id, payload
        )


@router.delete("/terms/{term_id}")
async def delete_term(
    session: SessionDep,
    current_user: CurrentUser,
    term_id: int,
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return SemanticTermService(
            SqlModelTermRepository(session),
            SqlModelDomainRepository(session),
        ).delete_term(
            current_user.oid, term_id
        )


@router.delete("/terms")
async def delete_terms(
    session: SessionDep,
    current_user: CurrentUser,
    term_ids: list[int],
) -> dict[str, list[int]]:
    with map_semantic_errors_to_http():
        return SemanticTermService(
            SqlModelTermRepository(session),
            SqlModelDomainRepository(session),
        ).delete_terms(current_user.oid, term_ids)


@router.patch("/terms/{term_id}/enabled")
async def set_term_enabled(
    session: SessionDep,
    current_user: CurrentUser,
    term_id: int,
    enabled: bool,
) -> SemanticTerm:
    with map_semantic_errors_to_http():
        return SemanticTermService(
            SqlModelTermRepository(session),
            SqlModelDomainRepository(session),
        ).set_term_enabled(current_user.oid, term_id, enabled)
