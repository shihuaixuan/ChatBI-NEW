"""Research 动作校验、指纹和执行需求物化。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from apps.chatbi.errors import ResearchExecutionError
from apps.chatbi.models.dto.execution_requirement import (
    CalculationOperation,
    CalculationRequirement,
    ExecutionRequirement,
    ExecutionResultContract,
    ExecutionRoute,
    QueryRequirement,
)
from apps.chatbi.models.dto.research import (
    EvidenceLogicalColumn,
    EvidenceSnapshot,
    ResearchAction,
    ResearchBreakdownAction,
    ResearchCompareAction,
    ResearchFilterFromResultAction,
    ResearchRequirement,
)


@dataclass(frozen=True, slots=True)
class EvidenceColumnProjection:
    """已物化结果字段与模型可见逻辑列之间的确定映射。"""

    field: str
    logical_column: EvidenceLogicalColumn


@dataclass(frozen=True, slots=True)
class MaterializedResearchAction:
    """一个通过范围校验且可交给 Plan 证明的 Research 动作。"""

    fingerprint: str
    requirement: ExecutionRequirement
    purpose: str
    metric_refs: tuple[str, ...]
    dimension_refs: tuple[str, ...]
    time_roles: tuple[str, ...]
    columns: tuple[EvidenceColumnProjection, ...]


def initial_compare_action(requirement: ResearchRequirement) -> ResearchCompareAction:
    """现象确认固定比较全部目标指标的当前期和对比期。"""

    if set(requirement.time_roles) not in ({"current", "previous"}, {"single"}):
        raise ResearchExecutionError(ResearchExecutionError.COMPARISON_REQUIRED)
    return ResearchCompareAction(
        metric_refs=requirement.target_metric_refs,
        time_roles=(
            ("current", "previous")
            if set(requirement.time_roles) == {"current", "previous"}
            else ("single",)
        ),
    )


def materialize_research_action(
    *,
    action: ResearchAction,
    research_requirement: ResearchRequirement,
    runtime: dict[str, Any],
    asset_snapshot: dict[str, Any],
    evidence_by_result: dict[str, EvidenceSnapshot],
) -> MaterializedResearchAction:
    """把模型动作转换成完整 Plan 执行需求，不读取自然语言补充语义。"""

    if action.type not in research_requirement.allowed_actions:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_NOT_ALLOWED)
    raw_assets = asset_snapshot.get("research_assets")
    if not isinstance(raw_assets, dict):
        raise ResearchExecutionError(
            ResearchExecutionError.ACTION_MATERIALIZATION_FAILED,
            details={"reason": "RESEARCH_ASSET_SNAPSHOT_REQUIRED"},
        )
    if any(
        not isinstance(ref, str) or not isinstance(value, dict)
        for ref, value in raw_assets.items()
    ):
        raise ResearchExecutionError(
            ResearchExecutionError.ACTION_MATERIALIZATION_FAILED,
            details={"reason": "RESEARCH_ASSET_SNAPSHOT_INVALID"},
        )
    assets = {str(ref): dict(value) for ref, value in raw_assets.items()}
    fingerprint = research_action_fingerprint(action)
    if isinstance(action, ResearchCompareAction):
        return _materialize_comparison(
            action=action,
            research_requirement=research_requirement,
            runtime=runtime,
            asset_snapshot=asset_snapshot,
            assets=assets,
            fingerprint=fingerprint,
            extra_filters=(),
            purpose="确认研究目标指标的当前期与对比期变化",
        )
    if isinstance(action, ResearchBreakdownAction):
        if action.metric_ref not in research_requirement.target_metric_refs:
            raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
        if action.dimension_ref not in research_requirement.scope.dimension_refs:
            raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
        if action.calculation == "value" and action.time_roles != ("single",):
            raise ResearchExecutionError(
                ResearchExecutionError.ACTION_MATERIALIZATION_FAILED,
                details={"reason": "RESEARCH_PHASE2_PERIOD_CALCULATION_REQUIRED"},
            )
        return _materialize_comparison(
            action=ResearchCompareAction(
                metric_refs=(action.metric_ref,),
                time_roles=action.time_roles,
            ),
            research_requirement=research_requirement,
            runtime=runtime,
            asset_snapshot=asset_snapshot,
            assets=assets,
            fingerprint=fingerprint,
            extra_filters=(),
            dimension_ref=action.dimension_ref,
            calculation=action.calculation,
            purpose=f"按 {action.dimension_ref} 分解 {action.metric_ref} 的变化",
        )
    if isinstance(action, ResearchFilterFromResultAction):
        return _materialize_filter_from_result(
            action=action,
            research_requirement=research_requirement,
            runtime=runtime,
            asset_snapshot=asset_snapshot,
            assets=assets,
            evidence_by_result=evidence_by_result,
            fingerprint=fingerprint,
        )
    raise ResearchExecutionError(ResearchExecutionError.ACTION_NOT_ALLOWED)


def research_action_fingerprint(action: ResearchAction) -> str:
    """使用完整逻辑动作生成稳定去重指纹。"""

    payload = json.dumps(
        action.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _materialize_filter_from_result(
    *,
    action: ResearchFilterFromResultAction,
    research_requirement: ResearchRequirement,
    runtime: dict[str, Any],
    asset_snapshot: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    evidence_by_result: dict[str, EvidenceSnapshot],
    fingerprint: str,
) -> MaterializedResearchAction:
    source = evidence_by_result.get(action.source_result_id)
    if source is None:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SOURCE_UNKNOWN)
    if (
        action.target_dimension_ref not in source.dimension_refs
        or action.target_dimension_ref
        not in research_requirement.scope.allowed_filter_refs
    ):
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    row = _selected_evidence_row(source, action)
    dimension_index = next(
        (
            index
            for index, column in enumerate(source.logical_columns)
            if column.dimension_ref == action.target_dimension_ref
            and column.value_role == "group_key"
        ),
        None,
    )
    if dimension_index is None:
        raise ResearchExecutionError(
            ResearchExecutionError.ACTION_SOURCE_COLUMN_UNKNOWN
        )
    selected_value = next(
        (
            item.value
            for item in row.values
            if item.logical_column_index == dimension_index
        ),
        None,
    )
    if selected_value is None:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SOURCE_ROW_UNKNOWN)
    analysis = action.analysis
    allowed_metrics = {
        *research_requirement.target_metric_refs,
        *research_requirement.scope.driver_metric_refs,
    }
    if not set(analysis.metric_refs) <= allowed_metrics:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    if analysis.type == "breakdown" and (
        analysis.dimension_ref not in research_requirement.scope.dimension_refs
        or analysis.dimension_ref == action.target_dimension_ref
    ):
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    dynamic_filter: tuple[
        str,
        str,
        Any,
        Literal["where", "having"],
    ] = (
        action.target_dimension_ref,
        "eq",
        selected_value,
        "where",
    )
    return _materialize_comparison(
        action=ResearchCompareAction(
            metric_refs=analysis.metric_refs,
            time_roles=analysis.time_roles,
        ),
        research_requirement=research_requirement,
        runtime=runtime,
        asset_snapshot=asset_snapshot,
        assets=assets,
        fingerprint=fingerprint,
        extra_filters=(dynamic_filter,),
        dimension_ref=analysis.dimension_ref,
        calculation="difference",
        purpose=(
            f"使用 {source.evidence_id} 选中的 {action.target_dimension_ref} 对象"
            f"继续执行 {analysis.type} 分析"
        ),
    )


def _selected_evidence_row(
    source: EvidenceSnapshot,
    action: ResearchFilterFromResultAction,
) -> Any:
    order = action.row_selector.order_by
    if order != source.row_order:
        raise ResearchExecutionError(
            ResearchExecutionError.ACTION_SOURCE_COLUMN_UNKNOWN
        )
    if not any(
        column.metric_ref == order.metric_ref
        and column.value_role == order.value_role
        for column in source.logical_columns
    ):
        raise ResearchExecutionError(
            ResearchExecutionError.ACTION_SOURCE_COLUMN_UNKNOWN
        )
    rows = (
        source.top_rows
        if action.row_selector.direction == "desc"
        else source.bottom_rows
    )
    index = action.row_selector.rank - 1
    if index >= len(rows):
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SOURCE_ROW_UNKNOWN)
    return rows[index]


def _materialize_comparison(
    *,
    action: ResearchCompareAction,
    research_requirement: ResearchRequirement,
    runtime: dict[str, Any],
    asset_snapshot: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    fingerprint: str,
    extra_filters: tuple[
        tuple[str, str, Any, Literal["where", "having"]], ...
    ],
    purpose: str,
    dimension_ref: str | None = None,
    calculation: str = "difference",
) -> MaterializedResearchAction:
    allowed_metrics = {
        *research_requirement.target_metric_refs,
        *research_requirement.scope.driver_metric_refs,
    }
    if not set(action.metric_refs) <= allowed_metrics:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    if tuple(action.time_roles) not in {("current", "previous"), ("single",)}:
        raise ResearchExecutionError(ResearchExecutionError.COMPARISON_REQUIRED)
    metric_assets = [_required_asset(assets, ref, "METRIC") for ref in action.metric_refs]
    dimension_asset = (
        _required_asset(assets, dimension_ref, "DIMENSION")
        if dimension_ref is not None
        else None
    )
    model_ids = {
        int(item["model_id"])
        for item in (*metric_assets, *((dimension_asset,) if dimension_asset else ()))
    }
    if len(model_ids) != 1:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    model_id = next(iter(model_ids))
    bindings = {item.role: item for item in research_requirement.time_bindings}
    query_requirements: list[QueryRequirement] = []
    suffix = fingerprint[:12]
    filters = _filters(research_requirement, assets, extra_filters)
    for role in action.time_roles:
        binding = bindings.get(role)
        if binding is None and role != "single":
            raise ResearchExecutionError(ResearchExecutionError.COMPARISON_REQUIRED)
        time_requirement: dict[str, Any] | None = None
        if binding is not None:
            time_asset = _required_asset(assets, binding.dimension_ref, "DIMENSION")
            time_requirement = {
                **binding.model_dump(mode="json"),
                "column": time_asset["column"],
            }
        query_requirements.append(
            QueryRequirement(
                id=f"research_{suffix}_{role}",
                model_ref=f"MODEL:{model_id}",
                metrics=tuple(metric_assets),
                group_by=(dimension_asset,) if dimension_asset is not None else (),
                filters=filters,
                time=time_requirement,
                query_shape={"shape": "research_action"},
            )
        )
    if action.time_roles == ("single",):
        query = query_requirements[0]
        columns: list[EvidenceColumnProjection] = []
        if dimension_asset is not None and dimension_ref is not None:
            columns.append(
                EvidenceColumnProjection(
                    field=str(dimension_asset["column"]),
                    logical_column=EvidenceLogicalColumn(
                        dimension_ref=dimension_ref,
                        value_role="group_key",
                    ),
                )
            )
        columns.extend(
            EvidenceColumnProjection(
                field=str(asset["biz_name"]),
                logical_column=EvidenceLogicalColumn(
                    metric_ref=metric_ref,
                    value_role="value",
                ),
            )
            for metric_ref, asset in zip(
                action.metric_refs,
                metric_assets,
                strict=True,
            )
        )
        requirement = ExecutionRequirement(
            status="ready",
            route=ExecutionRoute(
                mode="plan",
                origin="research_action",
                reasons=("research_action", "baseline"),
            ),
            query_requirements=(query,),
            result_contract=ExecutionResultContract(
                primary_requirement_id=query.id,
                ordered_requirement_ids=(query.id,),
            ),
            runtime={**runtime, "research_action_fingerprint": fingerprint},
            asset_snapshot=asset_snapshot,
        )
        return MaterializedResearchAction(
            fingerprint=fingerprint,
            requirement=requirement,
            purpose=purpose,
            metric_refs=action.metric_refs,
            dimension_refs=(dimension_ref,) if dimension_ref is not None else (),
            time_roles=action.time_roles,
            columns=tuple(columns),
        )

    operation = (
        CalculationOperation.GROWTH_RATE
        if calculation == "growth_rate"
        else CalculationOperation.DIFFERENCE
    )
    calculation_id = f"research_{suffix}_{operation.value}"
    metric_fields = tuple(str(item["biz_name"]) for item in metric_assets)
    join_keys = (
        (str(dimension_asset["column"]),)
        if dimension_asset is not None
        else ()
    )
    calc = CalculationRequirement(
        id=calculation_id,
        type=operation,
        inputs=tuple(item.id for item in query_requirements),
        join_keys=join_keys,
        value_columns=metric_fields,
    )
    columns = []
    if dimension_asset is not None and dimension_ref is not None:
        columns.append(
            EvidenceColumnProjection(
                field=str(dimension_asset["column"]),
                logical_column=EvidenceLogicalColumn(
                    dimension_ref=dimension_ref,
                    value_role="group_key",
                ),
            )
        )
    result_role: Literal["growth_rate", "difference"] = (
        "growth_rate"
        if operation is CalculationOperation.GROWTH_RATE
        else "difference"
    )
    for metric_ref, field in zip(action.metric_refs, metric_fields, strict=True):
        columns.extend(
            (
                EvidenceColumnProjection(
                    field=f"{field}_current",
                    logical_column=EvidenceLogicalColumn(
                        metric_ref=metric_ref,
                        value_role="current",
                    ),
                ),
                EvidenceColumnProjection(
                    field=f"{field}_previous",
                    logical_column=EvidenceLogicalColumn(
                        metric_ref=metric_ref,
                        value_role="previous",
                    ),
                ),
                EvidenceColumnProjection(
                    field=f"{field}_{result_role}",
                    logical_column=EvidenceLogicalColumn(
                        metric_ref=metric_ref,
                        value_role=result_role,
                    ),
                ),
            )
        )
    requirement = ExecutionRequirement(
        status="ready",
        route=ExecutionRoute(
            mode="plan",
            origin="research_action",
            reasons=("research_action", operation.value),
        ),
        query_requirements=tuple(query_requirements),
        post_calculations=(calc,),
        result_contract=ExecutionResultContract(
            primary_requirement_id=calculation_id,
            ordered_requirement_ids=(calculation_id,),
        ),
        runtime={**runtime, "research_action_fingerprint": fingerprint},
        asset_snapshot=asset_snapshot,
    )
    return MaterializedResearchAction(
        fingerprint=fingerprint,
        requirement=requirement,
        purpose=purpose,
        metric_refs=action.metric_refs,
        dimension_refs=(dimension_ref,) if dimension_ref is not None else (),
        time_roles=action.time_roles,
        columns=tuple(columns),
    )


def _filters(
    requirement: ResearchRequirement,
    assets: dict[str, dict[str, Any]],
    extra_filters: tuple[
        tuple[str, str, Any, Literal["where", "having"]], ...
    ],
) -> tuple[dict[str, Any], ...]:
    bindings = [
        (item.target_ref, item.operator, item.value, item.stage)
        for item in requirement.immutable_filters
    ]
    bindings.extend(extra_filters)
    result: list[dict[str, Any]] = []
    for target_ref, operator, value, stage in bindings:
        asset = _required_asset(assets, target_ref, "DIMENSION")
        result.append(
            {
                "target_ref": target_ref,
                "asset_id": asset["asset_id"],
                "column": asset["column"],
                "operator": operator,
                "value": value,
                "stage": stage,
            }
        )
    return tuple(result)


def _required_asset(
    assets: dict[str, dict[str, Any]],
    ref: str | None,
    asset_type: str,
) -> dict[str, Any]:
    asset = assets.get(ref or "")
    if asset is None or asset.get("asset_type") != asset_type:
        raise ResearchExecutionError(
            ResearchExecutionError.ACTION_MATERIALIZATION_FAILED,
            details={"ref": ref, "expected_type": asset_type},
        )
    return asset


__all__ = [
    "EvidenceColumnProjection",
    "MaterializedResearchAction",
    "initial_compare_action",
    "materialize_research_action",
    "research_action_fingerprint",
]
