from fastapi import APIRouter, BackgroundTasks

from apps.retrieval.semantic_worker import process_semantic_index_jobs
from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.repository.sqlmodel.dataset_index_repository import (
    SqlModelDatasetIndexRepository,
)
from apps.semantic.services.dataset_index_service import (
    SemanticDatasetIndexService,
)
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")


@router.post("/datasets/{dataset_id}/index/rebuild")
async def rebuild_dataset_index(
    session: SessionDep,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
    dataset_id: int,
):
    with map_semantic_errors_to_http():
        result = SemanticDatasetIndexService(
            SqlModelDatasetIndexRepository(session)
        ).rebuild_index(
            current_user.oid,
            dataset_id,
        )
    background_tasks.add_task(process_semantic_index_jobs, result.job_ids)
    return {
        "dataset_id": result.dataset_id,
        "index_version": result.index_version,
        "retrieval_source_id": result.source_id,
        "retrieval_generation": result.generation,
        "retrieval_job_ids": list(result.job_ids),
        "retrieval_status": "queued",
    }
