"""用户记忆管理接口。"""

from fastapi import APIRouter, HTTPException, Query

from apps.memory.composition import (
    build_memory_evaluation_service,
    build_memory_service,
)
from apps.memory.errors import (
    MemoryNotFoundError,
    MemoryUsageNotFoundError,
    MemoryValidationError,
)
from apps.memory.models.dto import (
    MemoryCreateInput,
    MemoryEvaluationRequest,
    MemoryEvaluationResult,
    MemoryLayer,
    MemoryListResult,
    MemoryRecallComparison,
    MemoryRecord,
    MemoryUpdateInput,
    MemoryUsageAdoptionInput,
    MemoryUsageMetrics,
    MemoryUsageRecord,
)
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["ChatBI Memory"], prefix="/chatbi/memories")


@router.get("", response_model=MemoryListResult)
async def list_memories(
    session: SessionDep,
    current_user: CurrentUser,
    layer: MemoryLayer | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> MemoryListResult:
    return MemoryListResult(
        items=build_memory_service(session).list_active(
            current_user.oid,
            current_user.id,
            layer=layer,
            limit=limit,
        )
    )


@router.get("/metrics", response_model=MemoryUsageMetrics)
async def get_memory_usage_metrics(
    session: SessionDep,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=100),
) -> MemoryUsageMetrics:
    try:
        return build_memory_service(session).get_usage_metrics(
            current_user.oid,
            current_user.id,
            limit=limit,
        )
    except MemoryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/metrics/comparison", response_model=MemoryRecallComparison)
async def compare_memory_recall_variants(
    session: SessionDep,
    current_user: CurrentUser,
    min_evaluated_count: int | None = Query(default=None, ge=1, le=100000),
    max_adoption_drop: float | None = Query(default=None, ge=0, le=1),
) -> MemoryRecallComparison:
    try:
        return build_memory_service(session).compare_recall_variants(
            current_user.oid,
            current_user.id,
            min_evaluated_count=min_evaluated_count,
            max_adoption_drop=max_adoption_drop,
        )
    except MemoryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/usages/{usage_id}", response_model=MemoryUsageRecord)
async def mark_memory_usage_adopted(
    session: SessionDep,
    current_user: CurrentUser,
    usage_id: int,
    payload: MemoryUsageAdoptionInput,
) -> MemoryUsageRecord:
    try:
        return build_memory_service(session).mark_usage_adopted(
            current_user.oid,
            current_user.id,
            usage_id,
            payload.adopted,
        )
    except MemoryUsageNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MemoryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/evaluations", response_model=MemoryEvaluationResult)
async def evaluate_memories(
    _current_user: CurrentUser,
    payload: MemoryEvaluationRequest,
) -> MemoryEvaluationResult:
    """计算当前用户提交的记忆评测样本，不保存样本和评测结果。"""

    try:
        return build_memory_evaluation_service().evaluate(payload.samples)
    except MemoryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=MemoryRecord)
async def create_memory(
    session: SessionDep,
    current_user: CurrentUser,
    payload: MemoryCreateInput,
) -> MemoryRecord:
    try:
        return build_memory_service(session).create_manual(
            current_user.oid,
            current_user.id,
            payload,
        )
    except MemoryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{memory_id}", response_model=MemoryRecord)
async def get_memory(
    session: SessionDep,
    current_user: CurrentUser,
    memory_id: int,
) -> MemoryRecord:
    try:
        return build_memory_service(session).get_owned(
            current_user.oid,
            current_user.id,
            memory_id,
        )
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{memory_id}", response_model=MemoryRecord)
async def update_memory(
    session: SessionDep,
    current_user: CurrentUser,
    memory_id: int,
    payload: MemoryUpdateInput,
) -> MemoryRecord:
    try:
        return build_memory_service(session).update_manual(
            current_user.oid,
            current_user.id,
            memory_id,
            payload,
        )
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MemoryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{memory_id}/disable", response_model=MemoryRecord)
async def disable_memory(
    session: SessionDep,
    current_user: CurrentUser,
    memory_id: int,
) -> MemoryRecord:
    try:
        return build_memory_service(session).disable(
            current_user.oid,
            current_user.id,
            memory_id,
        )
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    session: SessionDep,
    current_user: CurrentUser,
    memory_id: int,
) -> None:
    try:
        build_memory_service(session).delete(
            current_user.oid,
            current_user.id,
            memory_id,
        )
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]
