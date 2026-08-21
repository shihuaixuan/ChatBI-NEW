from fastapi import APIRouter

from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.models.dto import DatasetPayload, DatasetResponse
from apps.semantic.models.orm import SemanticDataset
from apps.semantic.repository.sqlmodel.dataset_repository import (
    SqlModelDatasetRepository,
)
from apps.semantic.repository.sqlmodel.domain_repository import (
    SqlModelDomainRepository,
)
from apps.semantic.services.dataset_service import SemanticDatasetService
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")


@router.get("/datasets")
async def list_datasets(
    session: SessionDep, current_user: CurrentUser, domain_id: int | None = None
) -> list[DatasetResponse]:
    with map_semantic_errors_to_http():
        return SemanticDatasetService(
            SqlModelDatasetRepository(session),
            SqlModelDomainRepository(session),
        ).list_datasets(current_user.oid, domain_id)


@router.post("/datasets")
async def create_dataset(
    session: SessionDep, current_user: CurrentUser, payload: DatasetPayload
) -> SemanticDataset:
    with map_semantic_errors_to_http():
        return SemanticDatasetService(
            SqlModelDatasetRepository(session),
            SqlModelDomainRepository(session),
        ).create_dataset(current_user.oid, payload)


@router.put("/datasets/{dataset_id}")
async def update_dataset(
    session: SessionDep,
    current_user: CurrentUser,
    dataset_id: int,
    payload: DatasetPayload,
) -> SemanticDataset:
    with map_semantic_errors_to_http():
        return SemanticDatasetService(
            SqlModelDatasetRepository(session),
            SqlModelDomainRepository(session),
        ).update_dataset(current_user.oid, dataset_id, payload)


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(
    session: SessionDep, current_user: CurrentUser, dataset_id: int
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return SemanticDatasetService(
            SqlModelDatasetRepository(session),
            SqlModelDomainRepository(session),
        ).delete_dataset(current_user.oid, dataset_id)
