from fastapi import APIRouter

from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.models.dto import DomainPayload
from apps.semantic.repository.sqlmodel.domain_repository import (
    SqlModelDomainRepository,
)
from apps.semantic.services.domain_service import SemanticDomainService
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")


@router.get("/domains")
async def list_domains(session: SessionDep, current_user: CurrentUser):
    with map_semantic_errors_to_http():
        return SemanticDomainService(
            SqlModelDomainRepository(session)
        ).list_domains(current_user.oid)


@router.post("/domains")
async def create_domain(
    session: SessionDep, current_user: CurrentUser, payload: DomainPayload
):
    with map_semantic_errors_to_http():
        return SemanticDomainService(
            SqlModelDomainRepository(session)
        ).create_domain(
            current_user.oid, payload
        )


@router.put("/domains/{domain_id}")
async def update_domain(
    session: SessionDep,
    current_user: CurrentUser,
    domain_id: int,
    payload: DomainPayload,
):
    with map_semantic_errors_to_http():
        return SemanticDomainService(
            SqlModelDomainRepository(session)
        ).update_domain(
            current_user.oid, domain_id, payload
        )


@router.delete("/domains/{domain_id}")
async def delete_domain(session: SessionDep, current_user: CurrentUser, domain_id: int):
    with map_semantic_errors_to_http():
        return SemanticDomainService(
            SqlModelDomainRepository(session)
        ).delete_domain(
            current_user.oid, domain_id
        )
