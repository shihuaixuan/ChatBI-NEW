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
    ResearchAppliedFilter,
    ResearchBreakdownAction,
    ResearchCompareAction,
    ResearchContributionAction,
    ResearchDrilldownAction,
    ResearchFilterFromResultAction,
    ResearchFocusedAnalysis,
    ResearchHypothesis,
    ResearchRequirement,
    ResearchValidateHypothesisAction,
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
    action: ResearchAction
    requirement: ExecutionRequirement
    purpose: str
    metric_refs: tuple[str, ...]
    dimension_refs: tuple[str, ...]
    time_roles: tuple[str, ...]
    columns: tuple[EvidenceColumnProjection, ...]
    primary_requirement_id: str
    hypothesis_ids: tuple[str, ...] = ()
    applied_filters: tuple[ResearchAppliedFilter, ...] = ()


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
    hypotheses: tuple[ResearchHypothesis, ...] | None = None,
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
        if action.metric_ref not in {
            *research_requirement.target_metric_refs,
            *research_requirement.scope.driver_metric_refs,
        }:
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
            source_action=action,
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
    if isinstance(action, ResearchDrilldownAction):
        return _materialize_drilldown(
            action=action,
            research_requirement=research_requirement,
            runtime=runtime,
            asset_snapshot=asset_snapshot,
            assets=assets,
            evidence_by_result=evidence_by_result,
            fingerprint=fingerprint,
        )
    if isinstance(action, ResearchContributionAction):
        return _materialize_contribution(
            action=action,
            research_requirement=research_requirement,
            runtime=runtime,
            asset_snapshot=asset_snapshot,
            assets=assets,
            fingerprint=fingerprint,
        )
    if isinstance(action, ResearchValidateHypothesisAction):
        return _materialize_validate_hypothesis(
            action=action,
            research_requirement=research_requirement,
            runtime=runtime,
            asset_snapshot=asset_snapshot,
            assets=assets,
            evidence_by_result=evidence_by_result,
            fingerprint=fingerprint,
            hypotheses=hypotheses,
        )
    raise ResearchExecutionError(ResearchExecutionError.ACTION_NOT_ALLOWED)


def research_action_fingerprint(action: ResearchAction) -> str:
    """按研究方向生成稳定指纹，运行态引用不能绕过方向去重。"""

    normalized = action.model_dump(mode="json")
    if isinstance(action, ResearchValidateHypothesisAction):
        # 假设 ID 和引用 Evidence 只是本次验证的运行态归属；目标指标、
        # 驱动指标和维度相同即属于同一验证方向。
        normalized.pop("hypothesis_id", None)
        normalized.pop("evidence_ids", None)
    payload = json.dumps(
        normalized,
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
        extra_filters=(
            *(_filter_tuple(item) for item in source.applied_filters),
            dynamic_filter,
        ),
        dimension_ref=analysis.dimension_ref,
        calculation="difference",
        purpose=(
            f"使用 {source.evidence_id} 选中的 {action.target_dimension_ref} 对象"
            f"继续执行 {analysis.type} 分析"
        ),
        source_action=action,
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
        column.metric_ref == order.metric_ref and column.value_role == order.value_role
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


def _materialize_drilldown(
    *,
    action: ResearchDrilldownAction,
    research_requirement: ResearchRequirement,
    runtime: dict[str, Any],
    asset_snapshot: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    evidence_by_result: dict[str, EvidenceSnapshot],
    fingerprint: str,
) -> MaterializedResearchAction:
    """按治理层级和已有结果行生成下一层下钻查询。"""

    if not set(action.metric_refs) <= {
        *research_requirement.target_metric_refs,
        *research_requirement.scope.driver_metric_refs,
    }:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    hierarchy = next(
        (
            item
            for item in research_requirement.scope.hierarchies
            if item.id == action.hierarchy_id
        ),
        None,
    )
    if hierarchy is None:
        raise ResearchExecutionError(ResearchExecutionError.HIERARCHY_NOT_AVAILABLE)
    try:
        current_index = hierarchy.dimension_refs.index(action.current_dimension_ref)
        next_index = hierarchy.dimension_refs.index(action.next_dimension_ref)
    except ValueError as exc:
        raise ResearchExecutionError(
            ResearchExecutionError.ACTION_SCOPE_INVALID
        ) from exc
    if next_index != current_index + 1:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    source = evidence_by_result.get(action.source_result_id)
    if source is None:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SOURCE_UNKNOWN)
    if action.current_dimension_ref not in source.dimension_refs:
        raise ResearchExecutionError(
            ResearchExecutionError.ACTION_SOURCE_COLUMN_UNKNOWN
        )
    row = _selected_evidence_row(
        source,
        ResearchFilterFromResultAction(
            source_result_id=action.source_result_id,
            row_selector=action.row_selector,
            target_dimension_ref=action.current_dimension_ref,
            analysis=ResearchFocusedAnalysis(
                type="compare",
                metric_refs=action.metric_refs,
                time_roles=source.time_roles,
            ),
        ),
    )
    dimension_index = next(
        (
            index
            for index, column in enumerate(source.logical_columns)
            if column.dimension_ref == action.current_dimension_ref
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
    dynamic_filter: tuple[str, str, Any, Literal["where", "having"]] = (
        action.current_dimension_ref,
        "eq",
        selected_value,
        "where",
    )
    return _materialize_comparison(
        action=ResearchCompareAction(
            metric_refs=action.metric_refs,
            time_roles=source.time_roles or ("single",),
        ),
        research_requirement=research_requirement,
        runtime=runtime,
        asset_snapshot=asset_snapshot,
        assets=assets,
        fingerprint=fingerprint,
        extra_filters=(
            *(_filter_tuple(item) for item in source.applied_filters),
            dynamic_filter,
        ),
        purpose=(
            f"沿 {action.hierarchy_id} 从 {action.current_dimension_ref} "
            f"下钻到 {action.next_dimension_ref}"
        ),
        dimension_ref=action.next_dimension_ref,
        calculation=(
            "difference"
            if tuple(source.time_roles) == ("current", "previous")
            else "value"
        ),
        source_action=action,
    )


def _materialize_contribution(
    *,
    action: ResearchContributionAction,
    research_requirement: ResearchRequirement,
    runtime: dict[str, Any],
    asset_snapshot: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    fingerprint: str,
) -> MaterializedResearchAction:
    """把贡献度动作展开为四查询、两差值和一次贡献度计算。"""

    if action.metric_ref not in research_requirement.scope.contribution_metric_refs:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    if (
        action.dimension_ref
        not in research_requirement.scope.contribution_dimension_refs
    ):
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    if tuple(action.time_roles) != ("current", "previous"):
        raise ResearchExecutionError(ResearchExecutionError.COMPARISON_REQUIRED)
    metric = _required_asset(assets, action.metric_ref, "METRIC")
    dimension = _required_asset(assets, action.dimension_ref, "DIMENSION")
    if int(metric["model_id"]) != int(dimension["model_id"]):
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    model_id = int(metric["model_id"])
    bindings = {item.role: item for item in research_requirement.time_bindings}
    filters = _filters(research_requirement, assets, ())
    metric_field = str(metric["biz_name"])
    dimension_field = str(dimension["column"])
    suffix = fingerprint[:12]
    query_requirements: list[QueryRequirement] = []
    for scope_name, group_by in (("total", ()), ("breakdown", (dimension,))):
        for role in action.time_roles:
            binding = bindings.get(role)
            if binding is None:
                raise ResearchExecutionError(ResearchExecutionError.COMPARISON_REQUIRED)
            time_asset = _required_asset(assets, binding.dimension_ref, "DIMENSION")
            query_requirements.append(
                QueryRequirement(
                    id=f"research_{suffix}_{scope_name}_{role}",
                    model_ref=f"MODEL:{model_id}",
                    metrics=(metric,),
                    group_by=group_by,
                    filters=filters,
                    time={
                        **binding.model_dump(mode="json"),
                        "column": time_asset["column"],
                    },
                    query_shape={"shape": "research_contribution", "scope": scope_name},
                )
            )
    current_role, previous_role = action.time_roles
    total_current = f"research_{suffix}_total_{current_role}"
    total_previous = f"research_{suffix}_total_{previous_role}"
    breakdown_current = f"research_{suffix}_breakdown_{current_role}"
    breakdown_previous = f"research_{suffix}_breakdown_{previous_role}"
    total_difference = f"research_{suffix}_total_difference"
    breakdown_difference = f"research_{suffix}_breakdown_difference"
    contribution_id = f"research_{suffix}_contribution"
    calculations = (
        CalculationRequirement(
            id=total_difference,
            type=CalculationOperation.DIFFERENCE,
            inputs=(total_current, total_previous),
            value_columns=(metric_field,),
        ),
        CalculationRequirement(
            id=breakdown_difference,
            type=CalculationOperation.DIFFERENCE,
            inputs=(breakdown_current, breakdown_previous),
            join_keys=(dimension_field,),
            value_columns=(metric_field,),
        ),
        CalculationRequirement(
            id=contribution_id,
            type=CalculationOperation.CONTRIBUTION,
            inputs=(breakdown_difference, total_difference),
            options={
                "dimensions": [dimension_field],
                "difference_column": f"{metric_field}_difference",
                "total_difference_column": f"{metric_field}_difference",
                "output_column": f"{metric_field}_contribution",
                "reconciliation_tolerance": research_requirement.scope.contribution_tolerance,
            },
        ),
    )
    columns = (
        EvidenceColumnProjection(
            field=dimension_field,
            logical_column=EvidenceLogicalColumn(
                dimension_ref=action.dimension_ref,
                value_role="group_key",
            ),
        ),
        EvidenceColumnProjection(
            field=f"{metric_field}_difference",
            logical_column=EvidenceLogicalColumn(
                metric_ref=action.metric_ref,
                value_role="difference",
            ),
        ),
        EvidenceColumnProjection(
            field=f"{metric_field}_contribution",
            logical_column=EvidenceLogicalColumn(
                metric_ref=action.metric_ref,
                value_role="contribution",
            ),
        ),
    )
    requirement = ExecutionRequirement(
        status="ready",
        route=ExecutionRoute(
            mode="plan",
            origin="research_action",
            reasons=("research_action", "contribution"),
        ),
        query_requirements=tuple(query_requirements),
        post_calculations=calculations,
        result_contract=ExecutionResultContract(
            primary_requirement_id=contribution_id,
            ordered_requirement_ids=(contribution_id,),
        ),
        runtime={**runtime, "research_action_fingerprint": fingerprint},
        asset_snapshot=asset_snapshot,
    )
    return MaterializedResearchAction(
        fingerprint=fingerprint,
        action=action,
        requirement=requirement,
        purpose=f"计算 {action.dimension_ref} 对 {action.metric_ref} 变化的贡献度",
        metric_refs=(action.metric_ref,),
        dimension_refs=(action.dimension_ref,),
        time_roles=action.time_roles,
        columns=columns,
        primary_requirement_id=contribution_id,
    )


def _materialize_validate_hypothesis(
    *,
    action: ResearchValidateHypothesisAction,
    research_requirement: ResearchRequirement,
    runtime: dict[str, Any],
    asset_snapshot: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    evidence_by_result: dict[str, EvidenceSnapshot],
    fingerprint: str,
    hypotheses: tuple[ResearchHypothesis, ...] | None,
) -> MaterializedResearchAction:
    """只按已治理驱动关系生成假设验证查询，不接受自由公式。"""

    if not action.evidence_ids:
        raise ResearchExecutionError(
            ResearchExecutionError.HYPOTHESIS_EVIDENCE_REQUIRED
        )
    hypothesis = next(
        (item for item in hypotheses or () if item.id == action.hypothesis_id),
        None,
    )
    if hypothesis is None or hypothesis.status.value not in {"pending", "inconclusive"}:
        raise ResearchExecutionError(ResearchExecutionError.HYPOTHESIS_INVALID)
    known_evidence_ids = {item.evidence_id for item in evidence_by_result.values()}
    if not set(action.evidence_ids) <= known_evidence_ids:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SOURCE_UNKNOWN)
    allowed_metrics = {
        *research_requirement.target_metric_refs,
        *research_requirement.scope.driver_metric_refs,
    }
    if not set(action.metric_refs) <= allowed_metrics:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    relationships = research_requirement.scope.driver_relationships
    if not any(
        set(action.metric_refs) == _relationship_metric_refs(item)
        and set(action.dimension_refs) <= set(item.dimension_refs)
        and set(research_requirement.time_roles) <= set(item.time_roles)
        for item in relationships
    ):
        raise ResearchExecutionError(ResearchExecutionError.HYPOTHESIS_INVALID)
    if not set(action.dimension_refs) <= set(research_requirement.scope.dimension_refs):
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    time_roles = research_requirement.time_roles
    return _materialize_comparison(
        action=ResearchCompareAction(
            metric_refs=action.metric_refs,
            time_roles=time_roles,
        ),
        research_requirement=research_requirement,
        runtime=runtime,
        asset_snapshot=asset_snapshot,
        assets=assets,
        fingerprint=fingerprint,
        extra_filters=(),
        dimension_refs=action.dimension_refs,
        purpose=f"验证假设 {action.hypothesis_id} 的驱动指标变化",
        calculation=(
            "difference" if tuple(time_roles) == ("current", "previous") else "value"
        ),
        source_action=action,
        hypothesis_ids=(action.hypothesis_id,),
    )


def _materialize_comparison(
    *,
    action: ResearchCompareAction,
    research_requirement: ResearchRequirement,
    runtime: dict[str, Any],
    asset_snapshot: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    fingerprint: str,
    extra_filters: tuple[tuple[str, str, Any, Literal["where", "having"]], ...],
    purpose: str,
    dimension_ref: str | None = None,
    dimension_refs: tuple[str, ...] = (),
    calculation: str = "difference",
    source_action: ResearchAction | None = None,
    hypothesis_ids: tuple[str, ...] = (),
) -> MaterializedResearchAction:
    allowed_metrics = {
        *research_requirement.target_metric_refs,
        *research_requirement.scope.driver_metric_refs,
    }
    if not set(action.metric_refs) <= allowed_metrics:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    if tuple(action.time_roles) not in {("current", "previous"), ("single",)}:
        raise ResearchExecutionError(ResearchExecutionError.COMPARISON_REQUIRED)
    metric_assets = [
        _required_asset(assets, ref, "METRIC") for ref in action.metric_refs
    ]
    selected_dimension_refs = tuple(
        dict.fromkeys(
            (
                *((dimension_ref,) if dimension_ref is not None else ()),
                *dimension_refs,
            )
        )
    )
    cross_model_relationship = _cross_model_relationship(
        action.metric_refs,
        research_requirement,
    )
    if cross_model_relationship is not None:
        return _materialize_cross_model_comparison(
            action=action,
            research_requirement=research_requirement,
            runtime=runtime,
            asset_snapshot=asset_snapshot,
            assets=assets,
            fingerprint=fingerprint,
            extra_filters=extra_filters,
            purpose=purpose,
            dimension_refs=selected_dimension_refs,
            calculation=calculation,
            source_action=source_action,
            hypothesis_ids=hypothesis_ids,
            relationship=cross_model_relationship,
        )
    _validate_driver_metric_scope(
        metric_refs=action.metric_refs,
        dimension_refs=selected_dimension_refs,
        time_roles=action.time_roles,
        requirement=research_requirement,
    )
    dimension_assets = tuple(
        _required_asset(assets, ref, "DIMENSION") for ref in selected_dimension_refs
    )
    model_ids = {int(item["model_id"]) for item in (*metric_assets, *dimension_assets)}
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
                group_by=dimension_assets,
                filters=filters,
                time=time_requirement,
                query_shape={"shape": "research_action"},
            )
        )
    if action.time_roles == ("single",):
        query = query_requirements[0]
        columns: list[EvidenceColumnProjection] = []
        columns.extend(
            EvidenceColumnProjection(
                field=str(asset["column"]),
                logical_column=EvidenceLogicalColumn(
                    dimension_ref=ref,
                    value_role="group_key",
                ),
            )
            for ref, asset in zip(
                selected_dimension_refs, dimension_assets, strict=True
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
            action=source_action or action,
            requirement=requirement,
            purpose=purpose,
            metric_refs=action.metric_refs,
            dimension_refs=selected_dimension_refs,
            time_roles=action.time_roles,
            columns=tuple(columns),
            primary_requirement_id=query.id,
            hypothesis_ids=hypothesis_ids,
            applied_filters=tuple(
                ResearchAppliedFilter(
                    target_ref=target_ref,
                    operator=operator,
                    value=value,
                    stage=stage,
                )
                for target_ref, operator, value, stage in extra_filters
            ),
        )

    operation = (
        CalculationOperation.GROWTH_RATE
        if calculation == "growth_rate"
        else CalculationOperation.DIFFERENCE
    )
    calculation_id = f"research_{suffix}_{operation.value}"
    metric_fields = tuple(str(item["biz_name"]) for item in metric_assets)
    join_keys = tuple(str(item["column"]) for item in dimension_assets)
    calc = CalculationRequirement(
        id=calculation_id,
        type=operation,
        inputs=tuple(item.id for item in query_requirements),
        join_keys=join_keys,
        value_columns=metric_fields,
    )
    columns = []
    columns.extend(
        EvidenceColumnProjection(
            field=str(asset["column"]),
            logical_column=EvidenceLogicalColumn(
                dimension_ref=ref,
                value_role="group_key",
            ),
        )
        for ref, asset in zip(selected_dimension_refs, dimension_assets, strict=True)
    )
    result_role: Literal["growth_rate", "difference"] = (
        "growth_rate" if operation is CalculationOperation.GROWTH_RATE else "difference"
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
        action=source_action or action,
        requirement=requirement,
        purpose=purpose,
        metric_refs=action.metric_refs,
        dimension_refs=selected_dimension_refs,
        time_roles=action.time_roles,
        columns=tuple(columns),
        primary_requirement_id=calculation_id,
        hypothesis_ids=hypothesis_ids,
        applied_filters=tuple(
            ResearchAppliedFilter(
                target_ref=target_ref,
                operator=operator,
                value=value,
                stage=stage,
            )
            for target_ref, operator, value, stage in extra_filters
        ),
    )


def _filters(
    requirement: ResearchRequirement,
    assets: dict[str, dict[str, Any]],
    extra_filters: tuple[tuple[str, str, Any, Literal["where", "having"]], ...],
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


def _cross_model_relationship(
    metric_refs: tuple[str, ...],
    requirement: ResearchRequirement,
) -> Any | None:
    """查找当前动作唯一对应的跨模型治理关系。"""

    if len(metric_refs) != 2:
        return None
    for relationship in requirement.scope.driver_relationships:
        if relationship.relationship_type == "formula_component":
            continue
        if {relationship.target_metric_ref, relationship.driver_metric_ref} != set(
            metric_refs
        ):
            continue
        target_model = _asset_model_id(relationship.target_metric_ref)
        driver_model = _asset_model_id(relationship.driver_metric_ref)
        if (
            target_model is not None
            and driver_model is not None
            and target_model != driver_model
        ):
            return relationship
    return None


def _materialize_cross_model_comparison(
    *,
    action: ResearchCompareAction,
    research_requirement: ResearchRequirement,
    runtime: dict[str, Any],
    asset_snapshot: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    fingerprint: str,
    extra_filters: tuple[tuple[str, str, Any, Literal["where", "having"]], ...],
    purpose: str,
    dimension_refs: tuple[str, ...],
    calculation: str,
    source_action: ResearchAction | None,
    hypothesis_ids: tuple[str, ...],
    relationship: Any,
) -> MaterializedResearchAction:
    """按模型拆分跨模型驱动验证，再用显式 MERGE 对齐结果。"""

    target_ref = relationship.target_metric_ref
    driver_ref = relationship.driver_metric_ref
    target_model = _asset_model_id(target_ref)
    driver_model = _asset_model_id(driver_ref)
    if target_model is None or driver_model is None or target_model == driver_model:
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)
    if not set(dimension_refs) <= set(relationship.dimension_refs):
        raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)

    target_dimensions = tuple(
        relationship.dimension_refs_by_model.get(str(target_model), ())
    )
    driver_dimensions = tuple(
        relationship.dimension_refs_by_model.get(str(driver_model), ())
    )
    if len(target_dimensions) != len(driver_dimensions):
        raise ResearchExecutionError(
            ResearchExecutionError.ACTION_MATERIALIZATION_FAILED,
            details={"reason": "CROSS_MODEL_DIMENSION_ALIGNMENT_INVALID"},
        )
    selected = set(dimension_refs)
    selected_indexes = tuple(
        index
        for index, (left, right) in enumerate(
            zip(target_dimensions, driver_dimensions, strict=True)
        )
        if not selected or left in selected or right in selected
    )
    selected_pairs = tuple(
        (target_dimensions[index], driver_dimensions[index])
        for index in selected_indexes
    )
    model_dimension_refs = {
        target_model: tuple(left for left, _ in selected_pairs),
        driver_model: tuple(right for _, right in selected_pairs),
    }
    aliases_by_model: dict[int, dict[int, str]] = {target_model: {}, driver_model: {}}
    for index, (left, right) in enumerate(selected_pairs):
        alias = f"research_dimension_{index}"
        for model_id, ref in ((target_model, left), (driver_model, right)):
            asset = _required_asset(assets, ref, "DIMENSION")
            aliases_by_model[model_id][int(asset["asset_id"])] = alias

    metric_assets = {
        target_model: _required_asset(assets, target_ref, "METRIC"),
        driver_model: _required_asset(assets, driver_ref, "METRIC"),
    }
    query_requirements: list[QueryRequirement] = []
    suffix = fingerprint[:12]
    for model_id, _metric_ref in (
        (target_model, target_ref),
        (driver_model, driver_ref),
    ):
        bindings = _model_time_bindings(research_requirement, model_id)
        roles = action.time_roles
        if roles != ("single",) and set(bindings) != set(roles):
            raise ResearchExecutionError(ResearchExecutionError.COMPARISON_REQUIRED)
        group_by = tuple(
            {
                **_required_asset(assets, ref, "DIMENSION"),
                "result_name": aliases_by_model[model_id][
                    int(_required_asset(assets, ref, "DIMENSION")["asset_id"])
                ],
            }
            for ref in model_dimension_refs[model_id]
        )
        filters = _filters_for_model(
            research_requirement,
            assets,
            extra_filters,
            model_id,
        )
        for role in roles:
            binding = bindings.get(role)
            time_requirement = None
            if binding is not None:
                time_asset = _required_asset(assets, binding.dimension_ref, "DIMENSION")
                time_requirement = {
                    **binding.model_dump(mode="json"),
                    "column": time_asset["column"],
                }
            query_requirements.append(
                QueryRequirement(
                    id=f"research_{suffix}_{model_id}_{role}",
                    model_ref=f"MODEL:{model_id}",
                    metrics=(metric_assets[model_id],),
                    group_by=group_by,
                    filters=filters,
                    time=time_requirement,
                    query_shape={"shape": "research_cross_model"},
                    output_aliases=aliases_by_model[model_id],
                )
            )

    calculations: list[CalculationRequirement] = []
    result_roles: dict[
        str,
        Literal["value", "growth_rate", "difference"],
    ]
    if action.time_roles == ("single",):
        target_input = query_requirements[0].id
        driver_input = query_requirements[1].id
        target_output = target_input
        driver_output = driver_input
        result_roles = {target_ref: "value", driver_ref: "value"}
    else:
        target_current, target_previous = (
            query_requirements[0].id,
            query_requirements[1].id,
        )
        driver_current, driver_previous = (
            query_requirements[2].id,
            query_requirements[3].id,
        )
        target_output = f"research_{suffix}_target_{calculation}"
        driver_output = f"research_{suffix}_driver_{calculation}"
        result_operation = (
            CalculationOperation.GROWTH_RATE
            if calculation == "growth_rate"
            else CalculationOperation.DIFFERENCE
        )
        calculations.extend(
            (
                CalculationRequirement(
                    id=target_output,
                    type=result_operation,
                    inputs=(target_current, target_previous),
                    join_keys=tuple(
                        f"research_dimension_{index}"
                        for index in range(len(selected_pairs))
                    ),
                    value_columns=(str(metric_assets[target_model]["biz_name"]),),
                ),
                CalculationRequirement(
                    id=driver_output,
                    type=result_operation,
                    inputs=(driver_current, driver_previous),
                    join_keys=tuple(
                        f"research_dimension_{index}"
                        for index in range(len(selected_pairs))
                    ),
                    value_columns=(str(metric_assets[driver_model]["biz_name"]),),
                ),
            )
        )
        result_roles = {
            target_ref: "growth_rate"
            if result_operation is CalculationOperation.GROWTH_RATE
            else "difference",
            driver_ref: "growth_rate"
            if result_operation is CalculationOperation.GROWTH_RATE
            else "difference",
        }
    merge_id = f"research_{suffix}_cross_model_merge"
    join_keys = tuple(
        f"research_dimension_{index}" for index in range(len(selected_pairs))
    )
    calculations.append(
        CalculationRequirement(
            id=merge_id,
            type=CalculationOperation.MERGE,
            inputs=(target_output, driver_output),
            join_keys=join_keys,
        )
    )
    columns: list[EvidenceColumnProjection] = []
    for index in range(len(selected_pairs)):
        columns.append(
            EvidenceColumnProjection(
                field=f"research_dimension_{index}",
                logical_column=EvidenceLogicalColumn(
                    dimension_ref=selected_pairs[index][0],
                    value_role="group_key",
                ),
            )
        )
    for metric_ref in (target_ref, driver_ref):
        field = str(
            metric_assets[target_model if metric_ref == target_ref else driver_model][
                "biz_name"
            ]
        )
        role = result_roles[metric_ref]
        columns.append(
            EvidenceColumnProjection(
                field=field if role == "value" else f"{field}_{role}",
                logical_column=EvidenceLogicalColumn(
                    metric_ref=metric_ref,
                    value_role=role,
                ),
            )
        )
    requirement = ExecutionRequirement(
        status="ready",
        route=ExecutionRoute(
            mode="plan",
            origin="research_action",
            reasons=("research_action", "cross_model_relation", "explicit_merge"),
        ),
        query_requirements=tuple(query_requirements),
        post_calculations=tuple(calculations),
        result_contract=ExecutionResultContract(
            primary_requirement_id=merge_id,
            ordered_requirement_ids=(merge_id,),
        ),
        runtime={
            **runtime,
            "research_action_fingerprint": fingerprint,
            "cross_model_relation_path": list(relationship.relation_path),
            "cross_model_dimension_refs_by_model": {
                str(model_id): list(refs)
                for model_id, refs in model_dimension_refs.items()
            },
        },
        asset_snapshot=asset_snapshot,
    )
    return MaterializedResearchAction(
        fingerprint=fingerprint,
        action=source_action or action,
        requirement=requirement,
        purpose=purpose,
        metric_refs=(target_ref, driver_ref),
        dimension_refs=tuple(ref for pair in selected_pairs for ref in pair),
        time_roles=action.time_roles,
        columns=tuple(columns),
        primary_requirement_id=merge_id,
        hypothesis_ids=hypothesis_ids,
        applied_filters=tuple(
            ResearchAppliedFilter(
                target_ref=target_ref,
                operator=operator,
                value=value,
                stage=stage,
            )
            for target_ref, operator, value, stage in extra_filters
        ),
    )


def _filters_for_model(
    requirement: ResearchRequirement,
    assets: dict[str, dict[str, Any]],
    extra_filters: tuple[tuple[str, str, Any, Literal["where", "having"]], ...],
    model_id: int,
) -> tuple[dict[str, Any], ...]:
    """只把属于当前模型的筛选条件放入该模型的独立查询。"""

    filters = _filters(requirement, assets, extra_filters)
    return tuple(
        item
        for item in filters
        if int(assets[str(item["target_ref"])]["model_id"]) == model_id
    )


def _model_time_bindings(
    requirement: ResearchRequirement,
    model_id: int,
) -> dict[str, Any]:
    bindings = requirement.time_bindings_by_model.get(str(model_id))
    if bindings:
        return {item.role: item for item in bindings}
    return {
        item.role: item
        for item in requirement.time_bindings
        if _asset_model_id(item.dimension_ref) == model_id
    }


def _asset_model_id(ref: str) -> int | None:
    """从受控资产引用读取模型 ID。"""

    parts = ref.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return None
    return int(parts[2])


def _validate_driver_metric_scope(
    *,
    metric_refs: tuple[str, ...],
    dimension_refs: tuple[str, ...],
    time_roles: tuple[str, ...],
    requirement: ResearchRequirement,
) -> None:
    """驱动指标只能在治理关系明确支持的共同维度和时间角色下查询。"""

    driver_refs = set(requirement.scope.driver_metric_refs)
    relationships = requirement.scope.driver_relationships
    for metric_ref in set(metric_refs) & driver_refs:
        if not any(
            metric_ref in (item.component_metric_refs or (item.driver_metric_ref,))
            and set(dimension_refs) <= set(item.dimension_refs)
            and set(time_roles) <= set(item.time_roles)
            for item in relationships
        ):
            raise ResearchExecutionError(ResearchExecutionError.ACTION_SCOPE_INVALID)


def _relationship_metric_refs(relationship: Any) -> set[str]:
    """返回一次关系验证必须同时查询的完整指标集合。"""

    return {
        relationship.target_metric_ref,
        *(relationship.component_metric_refs or (relationship.driver_metric_ref,)),
    }


def _filter_tuple(
    item: ResearchAppliedFilter,
) -> tuple[str, str, Any, Literal["where", "having"]]:
    return item.target_ref, item.operator, item.value, item.stage


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


def merge_research_action_requirements(
    actions: tuple[MaterializedResearchAction, ...],
) -> ExecutionRequirement:
    """把同轮独立动作合并为一个 Plan，让现有 DAG 执行器负责并行。"""

    if not actions:
        raise ResearchExecutionError(
            ResearchExecutionError.ACTION_MATERIALIZATION_FAILED
        )
    if len(actions) == 1:
        return actions[0].requirement
    primary_ids = tuple(item.primary_requirement_id for item in actions)
    if len(primary_ids) != len(set(primary_ids)):
        raise ResearchExecutionError(
            ResearchExecutionError.ACTION_MATERIALIZATION_FAILED
        )
    first, *supporting = primary_ids
    fingerprints = tuple(item.fingerprint for item in actions)
    base = actions[0].requirement
    return ExecutionRequirement(
        status="ready",
        route=base.route,
        query_requirements=tuple(
            query for item in actions for query in item.requirement.query_requirements
        ),
        post_calculations=tuple(
            calculation
            for item in actions
            for calculation in item.requirement.post_calculations
        ),
        result_contract=ExecutionResultContract(
            primary_requirement_id=first,
            supporting_requirement_ids=tuple(supporting),
            ordered_requirement_ids=primary_ids,
        ),
        runtime={
            **base.runtime,
            "research_action_fingerprints": fingerprints,
        },
        asset_snapshot=base.asset_snapshot,
    )


__all__ = [
    "EvidenceColumnProjection",
    "MaterializedResearchAction",
    "initial_compare_action",
    "merge_research_action_requirements",
    "materialize_research_action",
    "research_action_fingerprint",
]
