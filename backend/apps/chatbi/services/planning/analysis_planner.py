"""PLAN 模式分析计划生成器。"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from apps.chatbi.errors import QuestionModelError
from apps.chatbi.models.dto.analysis_plan import (
    AnalysisPlan,
    AnalysisPlanStatus,
    ComputeDerivation,
    ComputeOperation,
    ComputeTask,
    PlanEdge,
    PlanValidation,
    PresentationHint,
    QueryTask,
    QueryTaskSpec,
)
from apps.chatbi.models.dto.execution_requirement import (
    ExecutionRequirement,
    query_requirement_to_spec,
)
from apps.chatbi.models.dto.question_model import (
    QuestionModelInvocationData,
    QuestionModelJSONMode,
)
from apps.chatbi.services.planning.plan_prompts import (
    PLAN_SYSTEM_PROMPT,
    build_plan_prompt,
)
from apps.chatbi.services.planning.plan_validation import validate_analysis_plan
from apps.chatbi.services.understanding.model_invocation import StructuredModelService


class AnalysisPlanner:
    """规则优先生成计划；模型规划结果仍需经过同一 DTO 校验。"""

    def __init__(
        self,
        *,
        max_query_tasks: int = 5,
        model_service: StructuredModelService | None = None,
    ) -> None:
        self._max_query_tasks = max_query_tasks
        self._model_service = model_service

    def plan_from_semantic_state(
        self,
        *,
        plan_id: str,
        question_understanding: dict[str, Any],
        semantic_state: dict[str, Any],
    ) -> AnalysisPlan:
        if self._model_service is not None:
            return self._model_or_rule_plan(
                plan_id=plan_id,
                question_understanding=question_understanding,
                semantic_state=semantic_state,
            )
        return self._rule_plan_from_semantic_state(
            plan_id=plan_id,
            question_understanding=question_understanding,
            semantic_state=semantic_state,
        )

    def plan_from_execution_requirement(
        self,
        *,
        plan_id: str,
        requirement: ExecutionRequirement,
        dataset_id: int,
    ) -> AnalysisPlan:
        """只根据路由阶段产物生成计划，禁止规划阶段重新解释查询口径。"""

        try:
            requirement.require_ready("plan")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        query_tasks = tuple(
            QueryTask(
                id=f"q:{item.id}",
                source_requirement_id=item.id,
                spec=query_requirement_to_spec(item, dataset_id=dataset_id),
            )
            for item in requirement.query_requirements
        )
        query_ids = {item.id: f"q:{item.id}" for item in requirement.query_requirements}
        compute_tasks: list[ComputeTask] = []
        for calculation in requirement.post_calculations:
            operation = _compute_operation(calculation.type)
            # merge 只描述多查询输入的合并关系，当前计算引擎直接按输入完成合并。
            if operation is None:
                if calculation.type.strip().lower() != "merge":
                    raise ValueError(
                        f"PLAN_CALCULATION_OPERATION_NOT_ALLOWED:{calculation.type}"
                    )
                continue
            source_inputs = calculation.inputs or tuple(
                item
                for item in (calculation.current_input, calculation.previous_input)
                if item
            )
            inputs = tuple(query_ids.get(item, item) for item in source_inputs)
            if not inputs or any(item not in {task.id for task in query_tasks} for item in inputs):
                raise ValueError(f"PLAN_CALCULATION_INPUT_NOT_FOUND:{calculation.id}")
            details = dict(calculation.details)
            join_on = calculation.join_keys or tuple(
                str(item)
                for item in details.get("join_keys") or []
                if item
            )
            options = {
                key: value
                for key, value in details.items()
                if key in {
                    "dimensions",
                    "value_columns",
                    "dimension",
                    "top_n",
                    "limit",
                    "index",
                    "columns",
                    "column",
                }
            }
            derive = tuple(
                ComputeDerivation.model_validate(item)
                for item in details.get("derive") or []
                if isinstance(item, dict)
            )
            compute_tasks.append(
                ComputeTask(
                    id=f"c:{calculation.id}",
                    source_calculation_id=calculation.id,
                    operation=operation,
                    inputs=inputs,
                    join_on=join_on,
                    derive=derive,
                    options=options,
                )
            )
        tasks = (*query_tasks, *compute_tasks)
        if not query_tasks:
            raise ValueError("PLAN_QUERY_TASK_REQUIRED")
        edges = tuple(
            PlanEdge(source=input_id, target=task.id)
            for task in compute_tasks
            for input_id in task.inputs
        )
        primary = compute_tasks[-1].id if compute_tasks else query_tasks[0].id
        plan = AnalysisPlan(
            id=plan_id,
            tasks=tasks,
            edges=edges,
            presentation=PresentationHint(primary_result=primary),
            validation=PlanValidation(
                status=AnalysisPlanStatus.DRAFT,
                reports=({"planner_source": "execution_requirement"},),
            ),
        )
        validation = validate_analysis_plan(
            plan,
            max_query_tasks=self._max_query_tasks,
        )
        return plan.model_copy(update={"validation": validation})

    def _model_or_rule_plan(
        self,
        *,
        plan_id: str,
        question_understanding: dict[str, Any],
        semantic_state: dict[str, Any],
    ) -> AnalysisPlan:
        retry_reason: str | None = None
        for attempt in range(2):
            try:
                result = self._model_service.invoke(
                    QuestionModelInvocationData(
                        stage="analysis_planner",
                        system_prompt=PLAN_SYSTEM_PROMPT,
                        user_prompt=build_plan_prompt(
                            question_understanding=question_understanding,
                            semantic_state=semantic_state,
                            retry_reason=retry_reason,
                        ),
                        json_mode=QuestionModelJSONMode.STRICT,
                    )
                )
                plan = self.validate_model_plan(
                    result.payload,
                    max_query_tasks=self._max_query_tasks,
                ).model_copy(update={"id": plan_id})
                if plan.validation.status is AnalysisPlanStatus.REJECTED:
                    raise ValueError(
                        "PLAN_MODEL_VALIDATION_FAILED:"
                        + ",".join(plan.validation.reason_codes)
                    )
                self._validate_model_compute_contract(plan)
                self._validate_model_asset_scope(plan, semantic_state)
                return plan.model_copy(
                    update={
                        "validation": plan.validation.model_copy(
                            update={
                                "reports": (
                                    *plan.validation.reports,
                                    {"planner_source": "model", "attempt": attempt + 1},
                                )
                            }
                        )
                    }
                )
            except (QuestionModelError, ValidationError, ValueError) as exc:
                retry_reason = str(exc)
        # 模型两次都不能产出可执行计划时，规则规划仍会给出可审计的最小计划。
        fallback = self._rule_plan_from_semantic_state(
            plan_id=plan_id,
            question_understanding=question_understanding,
            semantic_state=semantic_state,
        )
        return fallback.model_copy(
            update={
                "validation": fallback.validation.model_copy(
                    update={
                        "reports": (
                            *fallback.validation.reports,
                            {
                                "planner_source": "deterministic_degraded",
                                "reason": retry_reason,
                            },
                        )
                    }
                )
            }
        )

    @staticmethod
    def _validate_model_compute_contract(plan: AnalysisPlan) -> None:
        """拒绝无法交给当前计算编译器的模型操作结构。"""

        for task in plan.tasks:
            if not isinstance(task, ComputeTask):
                continue
            if task.operation is ComputeOperation.SHARE:
                if len(task.inputs) != 1:
                    raise ValueError("PLAN_SHARE_INPUT_COUNT_INVALID")
                dimensions = task.options.get("dimensions")
                value_columns = task.options.get("value_columns")
                if not isinstance(dimensions, list) or not dimensions:
                    raise ValueError("PLAN_SHARE_DIMENSIONS_REQUIRED")
                if not isinstance(value_columns, list) or len(value_columns) != 1:
                    raise ValueError("PLAN_SHARE_VALUE_COLUMNS_REQUIRED")

    @staticmethod
    def _validate_model_asset_scope(
        plan: AnalysisPlan,
        semantic_state: dict[str, Any],
    ) -> None:
        """模型计划只能引用当前语义范围已经绑定的资产。"""

        scope = semantic_state.get("semantic_scope")
        scope = scope if isinstance(scope, dict) else {}
        allowed: set[int] = set()
        for query_plan in (
            scope.get("query_plan"),
            *(scope.get("query_plans") or []),
        ):
            if not isinstance(query_plan, dict):
                continue
            allowed.update(
                int(item["metric_id"])
                for item in query_plan.get("metrics") or []
                if isinstance(item, dict) and isinstance(item.get("metric_id"), int)
            )
            allowed.update(
                int(item["physical_dimension_id"])
                for item in query_plan.get("dimensions") or []
                if isinstance(item, dict)
                and isinstance(item.get("physical_dimension_id"), int)
            )
        if not allowed:
            compile_plan = scope.get("compile_plan")
            if isinstance(compile_plan, dict):
                allowed.update(int(item) for item in compile_plan.get("metric_asset_ids") or [])
                allowed.update(int(item) for item in compile_plan.get("dimension_asset_ids") or [])
        for task in plan.tasks:
            if not isinstance(task, QueryTask):
                continue
            if any(item not in allowed for item in (*task.spec.metric_ids, *task.spec.dimension_ids)):
                raise ValueError("PLAN_ASSET_NOT_ALLOWED")

    def _rule_plan_from_semantic_state(
        self,
        *,
        plan_id: str,
        question_understanding: dict[str, Any],
        semantic_state: dict[str, Any],
    ) -> AnalysisPlan:
        package = semantic_state.get("semantic_package")
        package = package if isinstance(package, dict) else {}
        multi_query_plans = package.get("multi_query_plans")
        time_ranges = _normalized_time_ranges(question_understanding)
        tasks: tuple[QueryTask, ...]
        if not isinstance(multi_query_plans, list) or not multi_query_plans:
            tasks = self._expand_time_range_tasks(
                (self._single_query_task("q1", semantic_state),),
                time_ranges,
            )
        else:
            scope = semantic_state.get("semantic_scope")
            scope = scope if isinstance(scope, dict) else {}
            dataset_id = int(scope.get("dataset_id") or semantic_state.get("dataset_id") or 0)
            base_tasks = tuple(
                self._multi_query_task(index, item, question_understanding, dataset_id)
                for index, item in enumerate(multi_query_plans, start=1)
                if isinstance(item, dict)
            )
            tasks = self._expand_time_range_tasks(base_tasks, time_ranges)
            if not tasks:
                tasks = (self._single_query_task("q1", semantic_state),)

        query_ids = tuple(task.id for task in tasks)
        shape = _query_shape(question_understanding)
        compute_task = self._compute_task(
            query_ids,
            shape,
            question_understanding,
            semantic_state,
        )
        all_tasks = (*tasks, *((compute_task,) if compute_task is not None else ()))
        edges = tuple(
            PlanEdge(source=input_id, target=compute_task.id)
            for input_id in (compute_task.inputs if compute_task is not None else ())
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
                    having=tuple(item for item in query_plan.get("having") or [] if isinstance(item, dict)),
                    time_offset=query_plan.get("time_offset") if isinstance(query_plan.get("time_offset"), dict) else None,
                ),
            )
        temporal = compile_plan.get("temporal_plan") or {}
        time_bucket = temporal.get("time_bucket") or {}
        temporal_filters = [
            item for item in temporal.get("filters") or [] if isinstance(item, dict)
        ]
        time_filter = next(
            (
                item
                for item in temporal_filters
                if isinstance(item.get("value"), dict)
                and item.get("value", {}).get("kind")
            ),
            None,
        )
        return QueryTask(
            id=task_id,
            spec=QueryTaskSpec(
                dataset_id=int(scope.get("dataset_id") or state.get("dataset_id") or 0),
                metric_ids=tuple(int(item) for item in compile_plan.get("metric_asset_ids") or []),
                dimension_ids=tuple(int(item) for item in compile_plan.get("dimension_asset_ids") or []),
                filters=tuple(
                    item
                    for item in [*(compile_plan.get("filters") or []), *temporal_filters]
                    if isinstance(item, dict)
                ),
                time_range=scope.get("normalized_time_range"),
                time_dimension_id=time_bucket.get("dimension_id")
                or (time_filter or {}).get("asset_id"),
                time_grain=time_bucket.get("grain"),
                query_shape="single_query",
                limit=compile_plan.get("limit"),
                having=tuple(item for item in compile_plan.get("having") or [] if isinstance(item, dict)),
                time_offset=compile_plan.get("time_offset") if isinstance(compile_plan.get("time_offset"), dict) else None,
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
        slots = slots if isinstance(slots, dict) else {}
        metrics = tuple(
            int(item["asset_id"])
            for item in slots.get("metrics") or []
            if isinstance(item, dict) and isinstance(item.get("asset_id"), int)
        )
        group_dimensions = tuple(
            int(item["asset_id"])
            for item in slots.get("group_dimensions") or []
            if isinstance(item, dict) and isinstance(item.get("asset_id"), int)
        )
        time_dimension_ids = _unique_ints(
            [
                int(item["asset_id"])
                for key in ("time_dimensions", "time_filters")
                for item in slots.get(key) or []
                if isinstance(item, dict) and isinstance(item.get("asset_id"), int)
            ]
        )
        payload_dimensions = _unique_ints(
            [
                int(item)
                for item in payload.get("dimension_ids") or []
                if isinstance(item, int) and not isinstance(item, bool)
            ]
        )
        if time_dimension_ids:
            # 严格语义计划把时间维度放在 time_binding，不能再作为普通输出维度匹配。
            dimensions = tuple(
                item for item in payload_dimensions if item not in time_dimension_ids
            )
            if not dimensions:
                dimensions = tuple(
                    item
                    for item in group_dimensions
                    if item not in time_dimension_ids
                )
        else:
            dimensions = group_dimensions or payload_dimensions
        filters = tuple(item for item in slots.get("dimension_filters") or [] if isinstance(item, dict))
        intent = question_understanding.get("intent") or {}
        time_range = intent.get("time_range") if isinstance(intent, dict) else None
        query_shape = intent.get("query_shape") if isinstance(intent, dict) else None
        query_shape = query_shape if isinstance(query_shape, dict) else {}
        return QueryTask(
            id=f"q{index}",
            spec=QueryTaskSpec(
                dataset_id=int(payload.get("dataset_id") or dataset_id),
                metric_ids=metrics or tuple(int(item) for item in payload.get("metric_ids") or []),
                dimension_ids=dimensions,
                filters=filters,
                time_range=(time_range or {}).get("normalized") if isinstance(time_range, dict) else None,
                time_dimension_id=time_dimension_ids[0] if time_dimension_ids else None,
                time_grain=query_shape.get("time_grain"),
                query_shape="single_query",
            ),
        )

    @staticmethod
    def _expand_time_range_tasks(
        tasks: tuple[QueryTask, ...],
        time_ranges: tuple[dict[str, Any], ...],
    ) -> tuple[QueryTask, ...]:
        if len(time_ranges) < 2:
            return tasks
        expanded: list[QueryTask] = []
        for task in tasks:
            for time_range in time_ranges:
                expanded.append(
                    task.model_copy(
                        update={
                            "id": f"q{len(expanded) + 1}",
                            "spec": task.spec.model_copy(
                                update={"time_range": time_range}
                            ),
                        }
                    )
                )
        return tuple(expanded)

    @staticmethod
    def _compute_task(
        query_ids: tuple[str, ...],
        shape: dict[str, Any],
        question_understanding: dict[str, Any],
        semantic_state: dict[str, Any],
    ) -> ComputeTask | None:
        intent = question_understanding.get("intent")
        intent = intent if isinstance(intent, dict) else {}
        intent_type = str(intent.get("intent_type") or "")
        package = semantic_state.get("semantic_package")
        package = package if isinstance(package, dict) else {}
        slot_bindings = package.get("slot_bindings")
        slot_bindings = slot_bindings if isinstance(slot_bindings, dict) else {}
        time_dimension_names = set(
            _slot_binding_names(slot_bindings, "time_dimensions")
            + _slot_binding_names(slot_bindings, "time_filters")
        )
        package_dimensions = [str(item) for item in package.get("dimensions") or [] if item]
        if intent_type in {"share_analysis", "composition"}:
            dimensions = _slot_binding_names(slot_bindings, "group_dimensions")
            if not dimensions:
                dimensions = [
                    item for item in package_dimensions if item not in time_dimension_names
                ]
            metrics = _slot_binding_names(slot_bindings, "metrics")
            if not metrics:
                metrics = [str(item) for item in package.get("metrics") or [] if item]
            options: dict[str, Any] = {}
            if dimensions:
                options["dimensions"] = dimensions
            if metrics:
                options["value_columns"] = metrics
            return ComputeTask(
                id="c1",
                operation=ComputeOperation.SHARE,
                inputs=(query_ids[0],),
                options=options,
            )
        comparison = intent.get("comparison")
        has_comparison = (
            isinstance(comparison, dict)
            or bool(shape.get("comparison"))
            or bool(shape.get("comparison_type"))
            or intent_type == "comparison_analysis"
        )
        if len(query_ids) < 2 or not (has_comparison or shape.get("needs_compute")):
            return None
        comparison_type = str(shape.get("comparison_type") or "")
        if not comparison_type and isinstance(comparison, dict):
            comparison_type = str(comparison.get("method") or "")
        operation = (
            ComputeOperation.GROWTH
            if comparison_type in {"growth", "yoy", "mom"}
            else ComputeOperation.COMPARE
        )
        join_dimensions = _slot_binding_names(slot_bindings, "business_dimensions")
        if not join_dimensions:
            join_dimensions = [
                item for item in package_dimensions if item not in time_dimension_names
            ]
        explicit_join_dimensions = shape.get("join_on")
        join_dimensions = (
            [str(item) for item in explicit_join_dimensions if item]
            if isinstance(explicit_join_dimensions, list) and explicit_join_dimensions
            else join_dimensions
        )
        return ComputeTask(
            id="c1",
            operation=operation,
            inputs=query_ids[:2],
            join_on=tuple(join_dimensions),
        )


def _slot_binding_names(slot_bindings: dict[str, Any], key: str) -> list[str]:
    """读取语义槽位绑定中的业务名称，避免使用包含时间维度的总维度列表。"""

    return [
        str(item["biz_name"])
        for item in slot_bindings.get(key) or []
        if isinstance(item, dict) and item.get("biz_name")
    ]


def _unique_ints(values: list[int]) -> tuple[int, ...]:
    """按原顺序去重资产 ID，保持严格子查询顺序稳定。"""

    return tuple(dict.fromkeys(values))


def _query_shape(understanding: dict[str, Any]) -> dict[str, Any]:
    intent = understanding.get("intent")
    if not isinstance(intent, dict):
        return {}
    shape = intent.get("query_shape")
    return shape if isinstance(shape, dict) else {}


def _normalized_time_ranges(
    understanding: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    intent = understanding.get("intent")
    if not isinstance(intent, dict):
        return ()
    result = []
    for item in intent.get("time_ranges") or []:
        if not isinstance(item, dict):
            continue
        normalized = item.get("normalized")
        if isinstance(normalized, dict):
            result.append(normalized)
    return tuple(result)


def _compute_operation(value: str) -> ComputeOperation | None:
    """把执行需求中的计算名称映射到计算引擎白名单。"""

    normalized = value.strip().lower()
    return {
        "difference": ComputeOperation.COMPARE,
        "compare": ComputeOperation.COMPARE,
        "growth": ComputeOperation.GROWTH,
        "growth_rate": ComputeOperation.GROWTH,
        "share": ComputeOperation.SHARE,
        "composition": ComputeOperation.SHARE,
        "topn_other": ComputeOperation.TOPN_OTHER,
        "pivot": ComputeOperation.PIVOT,
        "expr": ComputeOperation.EXPR,
        "ratio": ComputeOperation.EXPR,
    }.get(normalized)


__all__ = ["AnalysisPlanner"]
