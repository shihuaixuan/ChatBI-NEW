"""使用真实语义 Schema 和数据源手动验证完整规则 Plan 生命周期。"""

from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import Session

from apps.chatbi.composition import build_query_service
from apps.chatbi.models.dto.analysis_plan import (
    AnalysisPlanStatus,
    CompiledQuery,
    ComputeTask,
    QueryTask,
    ResultSetKind,
    ResultSetRef,
    ResultSetSnapshot,
    build_result_set_id,
)
from apps.chatbi.models.dto.execution_requirement import (
    ExecutionRequirement,
    QueryRequirement,
)
from apps.chatbi.models.dto.result_artifact import ChatBIResultArtifactRef
from apps.chatbi.models.dto.semantic_parse import SemanticParseOutput
from apps.chatbi.orchestration.pipeline.mode_router import ModeRouteInput, ModeRouter
from apps.chatbi.services.computation import ComputeEngine
from apps.chatbi.services.execution import (
    QueryTaskExecutionRequest,
    QueryTaskExecutionStatus,
    QueryTaskExecutor,
)
from apps.chatbi.services.planning.analysis_planner import AnalysisPlanner
from apps.chatbi.services.planning.dag_scheduler import build_execution_batches
from apps.chatbi.services.planning.plan_validation import validate_analysis_plan
from apps.datasource.models.dto.query import (
    DatasourceQueryRequest,
    DatasourceQueryStatus,
    DatasourceQuerySubject,
)
from apps.semantic import SemanticQueryPlanningInput, SemanticQueryPlanningService
from apps.semantic.composition import (
    build_semantic_schema_service,
    build_semantic_sql_compilation_service,
)
from apps.semantic.models.dto import DatasetSchema, SemanticPlanStatus
from apps.temporal import build_temporal_context
from apps.tool import NeverCancelled
from common.core.db import engine


class PlanCaseError(RuntimeError):
    """用例没有满足完整 Plan 生命周期的不变量。"""


@dataclass(frozen=True, slots=True)
class PlanCase:
    name: str
    description: str
    semantic_parse: dict[str, Any]
    expected_query_count: int
    expected_operations: tuple[str, ...]
    expected_route: str = "plan"


def _semantic_parse(
    *,
    measures: tuple[str, ...],
    group_by: tuple[str, ...] = (),
    time_filters: tuple[dict[str, str], ...] = (),
    calculations: tuple[dict[str, Any], ...] = (),
    multi_step: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造内置用例使用的已完成语义解析结果。"""

    return {
        "status": "resolved",
        "measures": [{"ref": ref} for ref in measures],
        "group_by": [{"ref": ref} for ref in group_by],
        "filters": [],
        "time_filters": list(time_filters),
        "order_by": [],
        "calculations": list(calculations),
        "multi_step": multi_step,
        "unresolved": [],
    }


CASES = (
    PlanCase(
        name="cross_model_merge",
        description="跨模型的总 GMV 与当前欠款余额合并",
        semantic_parse=_semantic_parse(
            measures=("METRIC:271:246", "METRIC:291:250"),
        ),
        expected_query_count=2,
        expected_operations=("merge",),
    ),
    PlanCase(
        name="period_difference",
        description="总 GMV 当前日与前一日差值",
        semantic_parse=_semantic_parse(
            measures=("METRIC:271:246",),
            time_filters=(
                {"expression": "2026年6月30日", "role": "current"},
                {"expression": "2026年6月29日", "role": "previous"},
            ),
            calculations=({"type": "difference"},),
        ),
        expected_query_count=2,
        expected_operations=("difference",),
    ),
    PlanCase(
        name="growth_rate",
        description="总 GMV 当前日与前一日增长率",
        semantic_parse=_semantic_parse(
            measures=("METRIC:271:246",),
            time_filters=(
                {"expression": "2026年6月30日", "role": "current"},
                {"expression": "2026年6月29日", "role": "previous"},
            ),
            calculations=({"type": "growth_rate"},),
        ),
        expected_query_count=2,
        expected_operations=("growth_rate",),
    ),
    PlanCase(
        name="share_by_stall",
        description="按档口计算总 GMV 占比",
        semantic_parse=_semantic_parse(
            measures=("METRIC:271:246",),
            group_by=("DIMENSION:278:246",),
            time_filters=({"expression": "2026年6月30日", "role": "current"},),
            calculations=({"type": "share"},),
        ),
        expected_query_count=1,
        expected_operations=("share",),
    ),
    PlanCase(
        name="metric_ratio",
        description="总 GMV 与销售订单数的确定性比率",
        semantic_parse=_semantic_parse(
            measures=("METRIC:271:246", "METRIC:263:246"),
            time_filters=({"expression": "2026年6月30日", "role": "current"},),
            calculations=(
                {
                    "type": "ratio",
                    "details": {
                        "numerator_ref": "METRIC:271:246",
                        "denominator_ref": "METRIC:263:246",
                        "result_name": "gmv_per_sales_order",
                    },
                },
            ),
        ),
        expected_query_count=1,
        expected_operations=("ratio",),
    ),
    PlanCase(
        name="topn_other",
        description="档口总 GMV Top 3 与其他项汇总",
        semantic_parse=_semantic_parse(
            measures=("METRIC:271:246",),
            group_by=("DIMENSION:278:246",),
            time_filters=({"expression": "2026年6月30日", "role": "current"},),
            calculations=(
                {
                    "type": "topn_other",
                    "details": {"dimension": "stall_id", "top_n": 3},
                },
            ),
        ),
        expected_query_count=1,
        expected_operations=("topn_other",),
    ),
    PlanCase(
        name="fixed_drilldown",
        description="总量与档口层级的固定下钻结果",
        semantic_parse=_semantic_parse(
            measures=("METRIC:271:246",),
            time_filters=({"expression": "2026年6月30日", "role": "single"},),
            multi_step={
                "type": "fixed_drilldown",
                "metric_refs": ["METRIC:271:246"],
                "levels": [
                    {
                        "id": "stall",
                        "dimension_refs": ["DIMENSION:278:246"],
                    }
                ],
                "include_total": True,
                "primary_level": "stall",
            },
        ),
        expected_query_count=2,
        expected_operations=(),
    ),
    PlanCase(
        name="fixed_attribution",
        description="总 GMV 日变化按档口进行固定贡献归因",
        semantic_parse=_semantic_parse(
            measures=("METRIC:271:246",),
            time_filters=(
                {"expression": "2026年6月30日", "role": "current"},
                {"expression": "2026年6月29日", "role": "previous"},
            ),
            multi_step={
                "type": "fixed_attribution",
                "metric_ref": "METRIC:271:246",
                "dimension_ref": "DIMENSION:278:246",
                "current_time_role": "current",
                "previous_time_role": "previous",
                "method": "additive_change_contribution",
            },
        ),
        expected_query_count=4,
        expected_operations=("difference", "difference", "contribution"),
    ),
    PlanCase(
        name="fast_boundary",
        description="单模型单查询必须停留在 Fast，不允许进入 Plan",
        semantic_parse=_semantic_parse(measures=("METRIC:271:246",)),
        expected_query_count=1,
        expected_operations=(),
        expected_route="fast",
    ),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="逐阶段输出并验证多个完整规则 Plan 用例。"
    )
    parser.add_argument("--tenant-id", type=int, required=True, help="租户 ID")
    parser.add_argument("--user-id", type=int, required=True, help="执行查询的用户 ID")
    parser.add_argument("--dataset-id", type=int, required=True, help="语义数据集 ID")
    parser.add_argument("--datasource-id", type=int, required=True, help="数据源 ID")
    parser.add_argument(
        "--case",
        action="append",
        choices=[case.name for case in CASES],
        help="指定用例，可重复；不传时执行全部用例",
    )
    parser.add_argument(
        "--reference-at",
        default="2026-08-19T12:00:00+08:00",
        help="固定时间解析基准，必须为带时区的 ISO 8601 时间",
    )
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--sample-rows", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=4, help="批次并发上限")
    parser.add_argument("--task-timeout", type=float, default=60.0, help="单批查询超时秒数")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="只运行到 PROVEN，不执行真实 SQL 和 ComputeTask",
    )
    parser.add_argument("--list-cases", action="store_true", help="列出用例后退出")
    return parser.parse_args()


def _print_stage(case: PlanCase, stage: str, payload: Any) -> None:
    print(f"\n--- [{case.name}] {stage} ---")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _candidate_groups(
    semantic_parse: SemanticParseOutput,
) -> dict[str, list[dict[str, str]]]:
    refs = dict.fromkeys(
        [
            *(item.ref for item in semantic_parse.measures),
            *(item.ref for item in semantic_parse.group_by),
            *(item.target_ref for item in semantic_parse.filters),
            *(item.target_ref for item in semantic_parse.order_by),
        ]
    )
    multi_step = semantic_parse.multi_step
    if multi_step is not None and multi_step.type == "fixed_drilldown":
        refs.update(dict.fromkeys(multi_step.metric_refs))
        refs.update(
            dict.fromkeys(
                ref for level in multi_step.levels for ref in level.dimension_refs
            )
        )
    elif multi_step is not None and multi_step.type == "fixed_attribution":
        refs.update(
            {
                multi_step.metric_ref: None,
                multi_step.dimension_ref: None,
            }
        )
    return {
        "metrics": [{"ref": ref} for ref in refs if ref.startswith("METRIC:")],
        "dimensions": [{"ref": ref} for ref in refs if ref.startswith("DIMENSION:")],
    }


def _semantic_planning_input(
    schema: DatasetSchema,
    requirement: QueryRequirement,
    dataset_id: int,
) -> SemanticQueryPlanningInput:
    """把完整查询需求投影为语义层的不可变查询计划输入。"""

    physical_dimensions = {item.id: item for item in schema.dimensions}
    logical_dimension_ids: list[int] = []
    dimension_usages: dict[int, tuple[str, ...]] = {}
    for item, usage in [
        *((item, "GROUP_BY") for item in requirement.group_by),
        *((item, "FILTER") for item in requirement.filters),
    ]:
        asset_id = item.get("asset_id")
        if not isinstance(asset_id, int) or isinstance(asset_id, bool):
            raise PlanCaseError("PLAN_CASE_DIMENSION_ASSET_ID_REQUIRED")
        physical = physical_dimensions.get(asset_id)
        logical_id = (
            physical.ext_info.get("logical_dimension_id")
            if physical is not None
            else None
        )
        if not isinstance(logical_id, int) or logical_id <= 0:
            raise PlanCaseError(f"PLAN_CASE_LOGICAL_DIMENSION_REQUIRED:{asset_id}")
        if logical_id not in logical_dimension_ids:
            logical_dimension_ids.append(logical_id)
        previous = dimension_usages.get(logical_id, ())
        if usage not in previous:
            dimension_usages[logical_id] = (*previous, usage)

    time = requirement.time or {}
    normalized_time = time.get("normalized")
    return SemanticQueryPlanningInput(
        dataset_id=dataset_id,
        schema_version=schema.schema_version,
        contract_version=schema.contract_version,
        metric_ids=tuple(int(item["asset_id"]) for item in requirement.metrics),
        logical_dimension_ids=tuple(logical_dimension_ids),
        dimension_usages=dimension_usages,
        filters=tuple(
            {
                "physical_dimension_id": item.get("asset_id"),
                "operator": item.get("operator") or "=",
                "value": item.get("value"),
                "value_source": "USER",
            }
            for item in requirement.filters
            if str(item.get("stage") or "where").lower() != "having"
        ),
        time_range=normalized_time if isinstance(normalized_time, dict) else None,
        time_dimension_id=(
            int(time["dimension_id"])
            if isinstance(time.get("dimension_id"), int)
            else None
        ),
        time_grain=str(time["grain"]) if time.get("grain") else None,
        select_mode=str(requirement.query_shape.get("select_mode") or "aggregate"),
        query_shape={
            "select_mode": requirement.query_shape.get("select_mode") or "aggregate",
            "needs_group_by": bool(requirement.group_by),
            **requirement.query_shape,
        },
        having=requirement.having,
        order_by=requirement.order_by,
        limit=requirement.limit,
        time_offset=requirement.time_offset,
    )


def _snapshot(
    *,
    plan_id: str,
    node_id: str,
    kind: ResultSetKind,
    fields: list[str] | tuple[str, ...],
    rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    source_sql: str,
) -> ResultSetSnapshot:
    """构造与 ResultStore 契约一致的内存快照，不写入持久化 Artifact。"""

    normalized_rows = tuple(dict(row) for row in rows)
    ref = ResultSetRef(
        result_set_id=build_result_set_id(plan_id, node_id),
        plan_id=plan_id,
        node_id=node_id,
        kind=kind,
        artifact_ref=ChatBIResultArtifactRef(
            artifact_id=f"manual:{plan_id}:{node_id}",
            kind="manual_plan_case",
            content_type="application/json",
            size=0,
            digest="manual-plan-case",
        ),
        fields=tuple(fields),
        row_count=len(normalized_rows),
        source_sql=source_sql,
    )
    return ResultSetSnapshot(ref=ref, rows=normalized_rows)


def _number(row: dict[str, Any], field: str) -> float:
    """读取结果数值，字段缺失或类型错误必须让用例失败。"""

    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise PlanCaseError(f"PLAN_CASE_NUMERIC_FIELD_INVALID:{field}")
    try:
        return float(value)
    except ValueError as exc:
        raise PlanCaseError(f"PLAN_CASE_NUMERIC_FIELD_INVALID:{field}") from exc


def _assert_primary_result(case: PlanCase, snapshot: ResultSetSnapshot) -> None:
    """按用例校验最终字段和关键计算关系，不只检查流程没有抛错。"""

    rows = [dict(row) for row in snapshot.rows]
    fields = set(snapshot.ref.fields)
    if case.name == "cross_model_merge":
        if len(rows) != 1 or not {"gmv_total", "arrears_amt"} <= fields:
            raise PlanCaseError("PLAN_CASE_CROSS_MODEL_RESULT_INVALID")
        return
    if case.name == "period_difference":
        required = {
            "gmv_total_current",
            "gmv_total_previous",
            "gmv_total_difference",
        }
        if len(rows) != 1 or not required <= fields:
            raise PlanCaseError("PLAN_CASE_DIFFERENCE_RESULT_INVALID")
        row = rows[0]
        expected = _number(row, "gmv_total_current") - _number(
            row, "gmv_total_previous"
        )
        if not math.isclose(
            _number(row, "gmv_total_difference"), expected, rel_tol=1e-9
        ):
            raise PlanCaseError("PLAN_CASE_DIFFERENCE_VALUE_INVALID")
        return
    if case.name == "growth_rate":
        required = {
            "gmv_total_current",
            "gmv_total_previous",
            "gmv_total_growth_rate",
        }
        if len(rows) != 1 or not required <= fields:
            raise PlanCaseError("PLAN_CASE_GROWTH_RESULT_INVALID")
        row = rows[0]
        previous = _number(row, "gmv_total_previous")
        expected = (_number(row, "gmv_total_current") - previous) / abs(previous)
        if not math.isclose(
            _number(row, "gmv_total_growth_rate"), expected, rel_tol=1e-9
        ):
            raise PlanCaseError("PLAN_CASE_GROWTH_VALUE_INVALID")
        return
    if case.name == "share_by_stall":
        if not rows or not {"stall_id", "gmv_total", "gmv_total_share"} <= fields:
            raise PlanCaseError("PLAN_CASE_SHARE_RESULT_INVALID")
        total_share = sum(_number(row, "gmv_total_share") for row in rows)
        if not math.isclose(total_share, 1.0, rel_tol=1e-9):
            raise PlanCaseError("PLAN_CASE_SHARE_TOTAL_INVALID")
        return
    if case.name == "metric_ratio":
        required = {"gmv_total", "order_cnt_sale", "gmv_per_sales_order"}
        if len(rows) != 1 or not required <= fields:
            raise PlanCaseError("PLAN_CASE_RATIO_RESULT_INVALID")
        row = rows[0]
        expected = _number(row, "gmv_total") / _number(row, "order_cnt_sale")
        if not math.isclose(
            _number(row, "gmv_per_sales_order"), expected, rel_tol=1e-9
        ):
            raise PlanCaseError("PLAN_CASE_RATIO_VALUE_INVALID")
        return
    if case.name == "topn_other":
        if (
            not rows
            or len(rows) > 4
            or not {
                "dimension_value",
                "metric_value",
            }
            <= fields
        ):
            raise PlanCaseError("PLAN_CASE_TOPN_RESULT_INVALID")
        if not any(str(row.get("dimension_value")) == "OTHER" for row in rows):
            raise PlanCaseError("PLAN_CASE_TOPN_OTHER_ROW_MISSING")
        return
    if case.name == "limited_difference_top3_other":
        if (
            len(rows) != 4
            or not {"dimension_value", "metric_value"} <= fields
            or not any(
                str(row.get("dimension_value")) == "OTHER" for row in rows
            )
        ):
            raise PlanCaseError("PLAN_CASE_LIMITED_TOPN_RESULT_INVALID")
        return
    if case.name == "limited_growth_rate_top3_other":
        if (
            len(rows) != 4
            or not {"dimension_value", "metric_value"} <= fields
            or not any(
                str(row.get("dimension_value")) == "OTHER" for row in rows
            )
        ):
            raise PlanCaseError("PLAN_CASE_LIMITED_GROWTH_TOPN_RESULT_INVALID")
        return
    if case.name == "fixed_drilldown":
        if not rows or not {"stall_id", "gmv_total"} <= fields:
            raise PlanCaseError("PLAN_CASE_DRILLDOWN_RESULT_INVALID")
        return
    if case.name == "fixed_attribution":
        required = {
            "stall_id",
            "gmv_total_difference",
            "total_difference",
            "gmv_total_contribution",
            "reconciliation_difference",
        }
        if not rows or not required <= fields:
            raise PlanCaseError("PLAN_CASE_ATTRIBUTION_RESULT_INVALID")
        if any(
            not math.isclose(
                _number(row, "reconciliation_difference"),
                0.0,
                abs_tol=1e-6,
            )
            for row in rows
        ):
            raise PlanCaseError("PLAN_CASE_ATTRIBUTION_RECONCILIATION_FAILED")
        contribution_total = sum(
            _number(row, "gmv_total_contribution") for row in rows
        )
        if not math.isclose(contribution_total, 1.0, abs_tol=1e-6):
            raise PlanCaseError("PLAN_CASE_ATTRIBUTION_TOTAL_INVALID")


def _run_case(
    case: PlanCase,
    args: argparse.Namespace,
    *,
    schema_provider: Any,
    sql_compilation_service: Any,
    query_service: Any,
    query_task_executor: QueryTaskExecutor,
    schema: DatasetSchema,
    temporal_context: Any,
    requirement_override: ExecutionRequirement | None = None,
) -> None:
    print(f"\n{'=' * 80}\n用例：{case.name}｜{case.description}\n{'=' * 80}")
    semantic_parse = SemanticParseOutput.model_validate(case.semantic_parse)
    candidates = _candidate_groups(semantic_parse)
    _print_stage(case, "1. SemanticParseOutput", semantic_parse.model_dump(mode="json"))
    _print_stage(case, "2. CandidateGroups", candidates)

    if requirement_override is None:
        route_payload = ModeRouter(schema_provider).route(
            ModeRouteInput(
                semantic_parse=semantic_parse,
                candidate_groups=candidates,
                dataset_id=args.dataset_id,
                tenant_id=args.tenant_id,
                enabled_modes=("fast", "plan"),
                temporal_context=temporal_context,
                datasource_id=args.datasource_id,
            )
        )
        requirement = ExecutionRequirement.model_validate(route_payload)
    else:
        requirement = requirement_override
    _print_stage(
        case, "3. ModeRouter 路由结果", requirement.route.model_dump(mode="json")
    )
    _print_stage(case, "4. ExecutionRequirement", requirement.model_dump(mode="json"))
    if requirement.route.mode != case.expected_route:
        raise PlanCaseError(
            f"PLAN_CASE_ROUTE_MISMATCH:{case.expected_route}:{requirement.route.mode}"
        )
    operations = tuple(item.type.value for item in requirement.post_calculations)
    if len(requirement.query_requirements) != case.expected_query_count:
        raise PlanCaseError("PLAN_CASE_QUERY_COUNT_MISMATCH")
    if operations != case.expected_operations:
        raise PlanCaseError("PLAN_CASE_OPERATIONS_MISMATCH")
    if case.expected_route == "fast":
        print(f"\n[{case.name}] 边界验证通过：该用例没有错误进入 Plan。")
        return
    requirement.require_ready("plan")

    plan_id = f"manual-{case.name}"
    draft_plan = AnalysisPlanner(max_query_tasks=10).plan(
        plan_id=plan_id,
        requirement=requirement,
        dataset_id=args.dataset_id,
    )
    _print_stage(case, "5. AnalysisPlan DRAFT", draft_plan.model_dump(mode="json"))
    if draft_plan.validation.status is not AnalysisPlanStatus.DRAFT:
        raise PlanCaseError("PLAN_CASE_DRAFT_VALIDATION_FAILED")

    query_by_id = {item.id: item for item in requirement.query_requirements}
    compiled_tasks: dict[str, QueryTask] = {}
    selected_tables: dict[str, list[str]] = {}
    for task in draft_plan.tasks:
        if not isinstance(task, QueryTask):
            continue
        source_id = task.source_requirement_id
        if source_id is None or source_id not in query_by_id:
            raise PlanCaseError("PLAN_CASE_QUERY_SOURCE_REQUIRED")
        planning_input = _semantic_planning_input(
            schema,
            query_by_id[source_id],
            args.dataset_id,
        )
        semantic_plan = SemanticQueryPlanningService().plan(schema, planning_input)
        if semantic_plan.validation_status is not SemanticPlanStatus.PROVEN:
            raise PlanCaseError(
                "PLAN_CASE_SEMANTIC_PLAN_NOT_PROVEN:"
                + ",".join(semantic_plan.validation_reason_codes)
            )
        _print_stage(
            case,
            f"6.1 QueryTask {task.id} 语义查询计划",
            semantic_plan.model_dump(mode="json"),
        )
        compiled_result = sql_compilation_service.compile_verified_plan(
            args.tenant_id,
            semantic_plan,
        )
        datasource_id = compiled_result.datasource_id or args.datasource_id
        validation = query_service.validate(
            DatasourceQueryRequest(
                sql=compiled_result.sql,
                datasource_id=datasource_id,
                subject=DatasourceQuerySubject(
                    user_id=args.user_id,
                    workspace_id=args.tenant_id,
                ),
                selected_tables=compiled_result.tables,
            )
        )
        if (
            validation.status is not DatasourceQueryStatus.SUCCEEDED
            or validation.data is None
        ):
            raise PlanCaseError(
                f"PLAN_CASE_SQL_VALIDATION_FAILED:{validation.error_code}"
            )
        compiled = CompiledQuery(
            plan_fingerprint=semantic_plan.fingerprint,
            sql=validation.data.sql,
            tables=tuple(compiled_result.tables),
        )
        compiled_tasks[task.id] = task.model_copy(update={"compiled": compiled})
        selected_tables[task.id] = list(compiled_result.tables)
        _print_stage(
            case,
            f"6.2 QueryTask {task.id} 编译和 SQL 校验",
            compiled.model_dump(mode="json"),
        )

    compiled_plan = draft_plan.model_copy(
        update={
            "tasks": tuple(
                compiled_tasks.get(task.id, task) for task in draft_plan.tasks
            )
        }
    )
    proven_validation = validate_analysis_plan(
        compiled_plan,
        max_query_tasks=10,
        require_proven=True,
    )
    if proven_validation.status is not AnalysisPlanStatus.PROVEN:
        raise PlanCaseError(
            "PLAN_CASE_PROOF_FAILED:" + ",".join(proven_validation.reason_codes)
        )
    proven_plan = compiled_plan.model_copy(update={"validation": proven_validation})
    _print_stage(case, "7. AnalysisPlan PROVEN", proven_plan.model_dump(mode="json"))
    if args.plan_only:
        print(f"\n[{case.name}] Plan-only 验证通过。")
        return

    batches = build_execution_batches(proven_plan)
    _print_stage(
        case,
        "8. DAG 拓扑批次",
        {"batches": [list(batch) for batch in batches]},
    )
    tasks = {task.id: task for task in proven_plan.tasks}
    snapshots: dict[str, ResultSetSnapshot] = {}
    compute_engine = ComputeEngine()
    for batch_index, batch in enumerate(batches, start=1):
        query_tasks = [
            tasks[task_id]
            for task_id in batch
            if isinstance(tasks[task_id], QueryTask)
        ]
        if query_tasks:
            requests = []
            for task in query_tasks:
                if task.compiled is None:
                    raise PlanCaseError("PLAN_CASE_QUERY_NOT_COMPILED")
                requests.append(
                    QueryTaskExecutionRequest(
                        task_id=task.id,
                        attempt=1,
                        sql=task.compiled.sql,
                        datasource_id=args.datasource_id,
                        workspace_id=args.tenant_id,
                        user_id=args.user_id,
                        selected_tables=tuple(selected_tables[task.id]),
                        deadline_monotonic=time.monotonic() + args.task_timeout,
                        cancellation=NeverCancelled(),
                    )
                )
            with ThreadPoolExecutor(
                max_workers=min(args.concurrency, len(requests))
            ) as pool:
                results = list(pool.map(query_task_executor.execute, requests))
            for result in results:
                if (
                    result.status is not QueryTaskExecutionStatus.SUCCEEDED
                    or result.data is None
                ):
                    raise PlanCaseError(
                        f"PLAN_CASE_QUERY_FAILED:{result.task_id}:{result.error_code}"
                    )
                snapshots[result.task_id] = _snapshot(
                    plan_id=plan_id,
                    node_id=result.task_id,
                    kind=ResultSetKind.QUERY,
                    fields=result.data.fields,
                    rows=result.data.full_data,
                    source_sql=result.data.sql,
                )
                _print_stage(
                    case,
                    f"9.{batch_index} QueryTask {result.task_id} 执行结果",
                    {
                        "batch": batch_index,
                        "attempt": result.attempt,
                        "result_set_ref": snapshots[
                            result.task_id
                        ].ref.model_dump(mode="json"),
                        "execution_ms": result.data.execution_ms,
                        "sample_rows": result.data.full_data[: args.sample_rows],
                    },
                )

        compute_tasks = [
            tasks[task_id]
            for task_id in batch
            if isinstance(tasks[task_id], ComputeTask)
        ]
        if compute_tasks:
            with ThreadPoolExecutor(
                max_workers=min(args.concurrency, len(compute_tasks))
            ) as pool:
                futures = [
                    pool.submit(
                        compute_engine.execute,
                        task,
                        {
                            input_id: snapshots[input_id]
                            for input_id in task.inputs
                        },
                    )
                    for task in compute_tasks
                ]
                computed_results = [future.result() for future in futures]
        else:
            computed_results = []
        for task, computed in zip(compute_tasks, computed_results, strict=True):
            snapshots[task.id] = _snapshot(
                plan_id=plan_id,
                node_id=task.id,
                kind=ResultSetKind.COMPUTE,
                fields=computed.fields,
                rows=computed.rows,
                source_sql=computed.sql,
            )
            _print_stage(
                case,
                f"9.{batch_index} ComputeTask {task.id} 执行结果",
                {
                    "batch": batch_index,
                    "attempt": 1,
                    "operation": task.operation.value,
                    "inputs": list(task.inputs),
                    "sql": computed.sql,
                    "result_set_ref": snapshots[task.id].ref.model_dump(mode="json"),
                    "sample_rows": list(computed.rows[: args.sample_rows]),
                },
            )

    primary_id = proven_plan.presentation.primary_result
    primary = snapshots.get(primary_id)
    if primary is None:
        raise PlanCaseError("PLAN_CASE_PRIMARY_RESULT_MISSING")
    _assert_primary_result(case, primary)
    _print_stage(
        case,
        "10. Primary Result",
        {
            "node_id": primary_id,
            "ref": primary.ref.model_dump(mode="json"),
            "sample_rows": list(primary.rows[: args.sample_rows]),
        },
    )
    print(f"\n[{case.name}] 完整 Plan 验证通过。")


def main() -> int:
    args = _parse_args()
    if args.list_cases:
        for case in CASES:
            print(f"{case.name}: {case.description}")
        return 0
    if min(args.tenant_id, args.user_id, args.dataset_id, args.datasource_id) <= 0:
        raise PlanCaseError("租户、用户、数据集和数据源 ID 必须为正整数")
    if args.sample_rows < 0:
        raise PlanCaseError("sample-rows 不能小于 0")
    if args.concurrency <= 0 or args.task_timeout <= 0:
        raise PlanCaseError("concurrency 和 task-timeout 必须大于 0")
    try:
        reference_at = datetime.fromisoformat(args.reference_at)
    except ValueError as exc:
        raise PlanCaseError("reference-at 不是合法的 ISO 8601 时间") from exc
    if reference_at.tzinfo is None or reference_at.utcoffset() is None:
        raise PlanCaseError("reference-at 必须包含时区")
    temporal_context = build_temporal_context(
        reference_at=reference_at,
        timezone=args.timezone,
    )
    selected_names = set(args.case or ())
    selected_cases = [
        case for case in CASES if not selected_names or case.name in selected_names
    ]

    with Session(engine) as session:
        schema_provider = build_semantic_schema_service(session)
        schema = schema_provider.build_dataset_schema(args.tenant_id, args.dataset_id)
        sql_compilation_service = build_semantic_sql_compilation_service(session)
        query_service = build_query_service(
            session,
            default_limit=1000,
            sample_rows=max(args.sample_rows, 10),
        )
        query_task_executor = QueryTaskExecutor(
            lambda: Session(engine),
            lambda worker_session: build_query_service(
                worker_session,
                default_limit=1000,
                sample_rows=max(args.sample_rows, 10),
            ),
        )
        print("\n=== 测试环境 ===")
        print(
            json.dumps(
                {
                    "tenant_id": args.tenant_id,
                    "user_id": args.user_id,
                    "dataset_id": args.dataset_id,
                    "datasource_id": args.datasource_id,
                    "schema_version": schema.schema_version,
                    "contract_version": schema.contract_version,
                    "schema_fingerprint": schema.schema_fingerprint,
                    "case_count": len(selected_cases),
                    "plan_only": args.plan_only,
                    "concurrency": args.concurrency,
                    "task_timeout": args.task_timeout,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        for case in selected_cases:
            _run_case(
                case,
                args,
                schema_provider=schema_provider,
                sql_compilation_service=sql_compilation_service,
                query_service=query_service,
                query_task_executor=query_task_executor,
                schema=schema,
                temporal_context=temporal_context,
            )

    print(f"\n全部通过：共完成 {len(selected_cases)} 个 Plan 分阶段用例。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PlanCaseError as exc:
        raise SystemExit(f"测试失败：{exc}") from exc
