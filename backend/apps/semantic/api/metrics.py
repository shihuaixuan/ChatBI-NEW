from fastapi import APIRouter
from sqlmodel import Session

from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.models.dto import MetricBatchCreateFromMeasuresPayload, MetricPayload
from apps.semantic.models.orm import SemanticMetric
from apps.semantic.repository.sqlmodel.metric_repository import (
    SqlModelMetricRepository,
)
from apps.semantic.repository.sqlmodel.model_repository import (
    SqlModelModelRepository,
)
from apps.semantic.services.metric_service import SemanticMetricService
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")


def _metric_service(session: Session) -> SemanticMetricService:
    return SemanticMetricService(
        SqlModelMetricRepository(session),
        SqlModelModelRepository(session),
    )


@router.get("/metrics")
async def list_metrics(
    session: SessionDep, current_user: CurrentUser, model_id: int | None = None
) -> list[SemanticMetric]:
    with map_semantic_errors_to_http():
        return _metric_service(session).list_metrics(current_user.oid, model_id)


@router.post("/metrics")
async def create_metric(
    session: SessionDep, current_user: CurrentUser, payload: MetricPayload
) -> SemanticMetric:
    with map_semantic_errors_to_http():
        return _metric_service(session).create_metric(current_user.oid, payload)


@router.post("/metrics/batch-create-from-measures")
async def batch_create_metrics_from_measures(
    session: SessionDep,
    current_user: CurrentUser,
    payload: MetricBatchCreateFromMeasuresPayload,
) -> dict[str, object]:
    with map_semantic_errors_to_http():
        return _metric_service(session).batch_create_from_measures(
            current_user.oid, payload
        )


@router.put("/metrics/{metric_id}")
async def update_metric(
    session: SessionDep,
    current_user: CurrentUser,
    metric_id: int,
    payload: MetricPayload,
) -> SemanticMetric:
    with map_semantic_errors_to_http():
        return _metric_service(session).update_metric(
            current_user.oid, metric_id, payload
        )


@router.delete("/metrics/{metric_id}")
async def delete_metric(
    session: SessionDep,
    current_user: CurrentUser,
    metric_id: int,
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return _metric_service(session).delete_metric(current_user.oid, metric_id)
