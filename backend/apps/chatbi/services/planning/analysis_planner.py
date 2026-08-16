"""PLAN 模式分析计划生成器。"""

from __future__ import annotations

from typing import Any

from apps.chatbi.models.dto.analysis_plan import (
    AnalysisPlan,
    AnalysisPlanStatus,
    ComputeOperation,
    ComputeTask,
    PlanEdge,
    PlanValidation,
    PresentationHint,
    QueryTask,
    QueryTaskSpec,
)
from apps.chatbi.services.planning.plan_validation import validate_analysis_plan


class AnalysisPlanner:
    """规则优先生成计划；模型规划结果仍需经过同一 DTO 校验。"""

    def __init__(self, *, max_query_tasks: int = 5) -> None:
        self._max_query_tasks = max_query_tasks

    def plan_from_semantic_state(
        self,
        *,
        plan_id: str,
        question_understanding: dict[str, Any],
        semantic_state: dict[str, Any],
    ) -> AnalysisPlan:
        package = semantic_state.get("semantic_package")
        package = package if isinstance(package, dict) else {}
        multi_query_plans = package.get("multi_query_plans")
        tasks: tuple[QueryTask, ...]
        if not isinstance(multi_query_plans, list) or not multi_query_plans:
            tasks = (self._single_query_task("q1", semantic_state),)
        else:
            scope = semantic_state.get("semantic_scope")
            scope = scope if isinstance(scope, dict) else {}
            dataset_id = int(scope.get("dataset_id") or semantic_state.get("dataset_id") or 0)
            tasks = tuple(
                self._multi_query_task(index, item, question_understanding, dataset_id)
                for index, item in enumerate(multi_query_plans, start=1)
                if isinstance(item, dict)
            )
            if not tasks:
                tasks = (self._single_query_task("q1", semantic_state),)

        query_ids = tuple(task.id for task in tasks)
        shape = _query_shape(question_understanding)
        compute_task = self._comparison_task(query_ids, shape)
        all_tasks = (*tasks, *((compute_task,) if compute_task is not None else ()))
        edges = tuple(
            PlanEdge(source=task.id, target=compute_task.id)
            for task in tasks
            if compute_task is not None
        )
        primary = compute_task.id if compute_task is not None else tasks[0].id
        plan = AnalysisPlan(
            id=plan_id,
            tasks=all_tasks,
            edges=edges,
            presentation=PresentationHint(
                primary_result=primary,
                chart_hint=str(shape.get("chart_hint") or "table") or None,
            ),
            validation=PlanValidation(status=AnalysisPlanStatus.DRAFT),
        )
        validation = validate_analysis_plan(
            plan,
            max_query_tasks=self._max_query_tasks,
        )
        return plan.model_copy(update={"validation": validation})

    @staticmethod
    def validate_model_plan(
        payload: dict[str, Any],
        *,
        max_query_tasks: int = 5,
    ) -> AnalysisPlan:
        """模型只负责产出结构，资产和 DAG 约束由这里统一验证。"""

        plan = AnalysisPlan.model_validate(payload)
        validation = validate_analysis_plan(plan, max_query_tasks=max_query_tasks)
        return plan.model_copy(update={"validation": validation})

    def _single_query_task(self, task_id: str, state: dict[str, Any]) -> QueryTask:
        scope = state.get("semantic_scope")
        scope = scope if isinstance(scope, dict) else {}
        compile_plan = scope.get("compile_plan")
        compile_plan = compile_plan if isinstance(compile_plan, dict) else {}
        query_plan = scope.get("query_plan")
        query_plan = query_plan if isinstance(query_plan, dict) else {}
        if query_plan:
            metrics = tuple(
                int(item["metric_id"])
                for item in query_plan.get("metrics") or []
                if isinstance(item, dict) and isinstance(item.get("metric_id"), int)
            )
            dimensions = tuple(
                int(item["physical_dimension_id"])
                for item in query_plan.get("dimensions") or []
                if isinstance(item, dict) and isinstance(item.get("physical_dimension_id"), int)
            )
            filters = tuple(item for item in query_plan.get("filters") or [] if isinstance(item, dict))
            return QueryTask(
                id=task_id,
                spec=QueryTaskSpec(
                    dataset_id=int(query_plan["dataset_id"]),
                    metric_ids=metrics,
                    dimension_ids=dimensions,
                    filters=filters,
                    time_range=(query_plan.get("time_binding") or {}).get("time_range"),
                    time_dimension_id=(query_plan.get("time_binding") or {}).get("dimension_id"),
                    time_grain=(query_plan.get("time_binding") or {}).get("grain"),
                    query_shape="single_query",
                    limit=query_plan.get("limit"),
                ),
            )
        temporal = compile_plan.get("temporal_plan") or {}
        time_bucket = temporal.get("time_bucket") or {}
        return QueryTask(
            id=task_id,
            spec=QueryTaskSpec(
                dataset_id=int(scope.get("dataset_id") or state.get("dataset_id") or 0),
                metric_ids=tuple(int(item) for item in compile_plan.get("metric_asset_ids") or []),
                dimension_ids=tuple(int(item) for item in compile_plan.get("dimension_asset_ids") or []),
                filters=tuple(
                    item
                    for item in [*(compile_plan.get("filters") or []), *(temporal.get("filters") or [])]
                    if isinstance(item, dict)
                ),
                time_range=scope.get("normalized_time_range"),
                time_dimension_id=time_bucket.get("dimension_id"),
                time_grain=time_bucket.get("grain"),
                query_shape="single_query",
                limit=compile_plan.get("limit"),
            ),
        )

    @staticmethod
    def _multi_query_task(
        index: int,
        payload: dict[str, Any],
        question_understanding: dict[str, Any],
        dataset_id: int,
    ) -> QueryTask:
        slots = payload.get("slots") or {}
        metrics = tuple(
            int(item["asset_id"])
            for item in slots.get("metrics") or []
            if isinstance(item, dict) and isinstance(item.get("asset_id"), int)
        )
        dimensions = tuple(
            int(item["asset_id"])
            for item in slots.get("group_dimensions") or []
            if isinstance(item, dict) and isinstance(item.get("asset_id"), int)
        )
        filters = tuple(item for item in slots.get("dimension_filters") or [] if isinstance(item, dict))
        intent = question_understanding.get("intent") or {}
        time_range = intent.get("time_range") if isinstance(intent, dict) else None
        return QueryTask(
            id=f"q{index}",
            spec=QueryTaskSpec(
                dataset_id=int(payload.get("dataset_id") or dataset_id),
                metric_ids=metrics or tuple(int(item) for item in payload.get("metric_ids") or []),
                dimension_ids=dimensions or tuple(int(item) for item in payload.get("dimension_ids") or []),
                filters=filters,
                time_range=(time_range or {}).get("normalized") if isinstance(time_range, dict) else None,
                query_shape="single_query",
            ),
        )

    @staticmethod
    def _comparison_task(
        query_ids: tuple[str, ...],
        shape: dict[str, Any],
    ) -> ComputeTask | None:
        if len(query_ids) < 2 or not (
            shape.get("comparison") or shape.get("comparison_type") or shape.get("needs_compute")
        ):
            return None
        operation = ComputeOperation.GROWTH if shape.get("comparison_type") in {"growth", "yoy", "mom"} else ComputeOperation.COMPARE
        return ComputeTask(
            id="c1",
            operation=operation,
            inputs=query_ids,
            join_on=tuple(str(item) for item in shape.get("join_on") or []),
        )


def _query_shape(understanding: dict[str, Any]) -> dict[str, Any]:
    intent = understanding.get("intent")
    if not isinstance(intent, dict):
        return {}
    shape = intent.get("query_shape")
    return shape if isinstance(shape, dict) else {}


__all__ = ["AnalysisPlanner"]
