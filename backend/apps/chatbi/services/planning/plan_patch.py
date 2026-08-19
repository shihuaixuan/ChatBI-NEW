"""多轮追问对既有 AnalysisPlan 的受控增量修改。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from apps.chatbi.models.dto.analysis_plan import (
    AnalysisPlan,
    AnalysisPlanStatus,
    PlanValidation,
    QueryTask,
)
from apps.chatbi.services.planning.plan_validation import validate_analysis_plan


class PlanPatchError(ValueError):
    """计划补丁不满足安全应用条件。"""


class PlanPatch(BaseModel):
    """只表达 P1 允许的时间、维度和筛选增量。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    replace_time_range: dict[str, Any] | None = None
    replace_dimension_ids: tuple[int, ...] | None = None
    add_filters: tuple[dict[str, Any], ...] = ()
    remove_filters: tuple[dict[str, Any], ...] = ()
    replace_filters: tuple[dict[str, Any], ...] | None = None
    target_task_ids: tuple[str, ...] = ()


class PlanPatchResult(BaseModel):
    """补丁结果，供编排层决定继续执行或回退完整理解。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: AnalysisPlan
    changed_task_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


def apply_plan_patch(
    plan: AnalysisPlan,
    patch: PlanPatch,
    *,
    max_query_tasks: int = 5,
) -> PlanPatchResult:
    """对所有目标 QueryTask 应用补丁，并重新执行计划校验。"""

    query_tasks = [task for task in plan.tasks if isinstance(task, QueryTask)]
    if not query_tasks:
        raise PlanPatchError("PLAN_PATCH_QUERY_TASK_REQUIRED")
    targets = set(patch.target_task_ids) or {task.id for task in query_tasks}
    unknown_targets = targets - {task.id for task in query_tasks}
    if unknown_targets:
        raise PlanPatchError("PLAN_PATCH_TARGET_UNKNOWN")
    if not (
        patch.replace_time_range is not None
        or patch.replace_dimension_ids is not None
        or bool(patch.add_filters)
        or bool(patch.remove_filters)
        or patch.replace_filters is not None
    ):
        raise PlanPatchError("PLAN_PATCH_EMPTY")

    changed: list[str] = []
    updated_tasks = []
    for task in plan.tasks:
        if not isinstance(task, QueryTask) or task.id not in targets:
            updated_tasks.append(task)
            continue
        spec = task.spec
        filters = list(spec.filters)
        if patch.replace_filters is not None:
            filters = [dict(item) for item in patch.replace_filters]
        filters = [
            item for item in filters if not _matches_any_filter(item, patch.remove_filters)
        ]
        filters.extend(dict(item) for item in patch.add_filters)
        updated_spec = spec.model_copy(
            update={
                key: value
                for key, value in {
                    "time_range": patch.replace_time_range,
                    "dimension_ids": patch.replace_dimension_ids,
                    "filters": tuple(filters),
                }.items()
                if value is not None
            }
        )
        updated_tasks.append(task.model_copy(update={"spec": updated_spec, "compiled": None}))
        changed.append(task.id)

    if not changed:
        raise PlanPatchError("PLAN_PATCH_NO_TASK_CHANGED")
    candidate = plan.model_copy(
        update={
            "version": plan.version + 1,
            "tasks": tuple(updated_tasks),
            "validation": PlanValidation(status=AnalysisPlanStatus.DRAFT),
        }
    )
    validation = validate_analysis_plan(candidate, max_query_tasks=max_query_tasks)
    if validation.status is AnalysisPlanStatus.REJECTED:
        raise PlanPatchError(
            "PLAN_PATCH_VALIDATION_FAILED:" + ",".join(validation.reason_codes)
        )
    return PlanPatchResult(
        plan=candidate.model_copy(update={"validation": validation}),
        changed_task_ids=tuple(changed),
        reason_codes=("PLAN_PATCH_APPLIED",),
    )


def patch_from_understanding(understanding: dict[str, Any]) -> PlanPatch | None:
    """只接受重写器显式给出的补丁，不根据自然语言自行猜测字段。"""

    payload = understanding.get("plan_patch")
    if not isinstance(payload, dict):
        return None
    try:
        return PlanPatch.model_validate(deepcopy(payload))
    except ValidationError as exc:
        raise PlanPatchError("PLAN_PATCH_PAYLOAD_INVALID") from exc


def _matches_any_filter(item: dict[str, Any], candidates: tuple[dict[str, Any], ...]) -> bool:
    """按显式键值匹配待删除筛选，避免模糊删除其他条件。"""

    return any(
        all(item.get(key) == value for key, value in candidate.items())
        for candidate in candidates
    )


__all__ = [
    "PlanPatch",
    "PlanPatchError",
    "PlanPatchResult",
    "apply_plan_patch",
    "patch_from_understanding",
]
