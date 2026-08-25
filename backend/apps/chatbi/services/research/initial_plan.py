"""Research 首轮基础计划的确定性生成器。

计划只使用路由期冻结的 Requirement，不读取用户原文，也不调用模型。
结果依赖的筛选、下钻和补证节点继续留给后续 Research 循环处理。
"""

from __future__ import annotations

from collections.abc import Iterable

from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchComputeOperation,
    ResearchEvidenceRequirement,
    ResearchInitialPlan,
    ResearchInitialPlanNode,
    ResearchQueryComparison,
    ResearchTimeRole,
)


class ResearchInitialPlanner:
    """Research Planner：只生成当前可确定的首轮部分计划。"""

    def plan(self, requirement: ResearchAgentRequirement) -> ResearchInitialPlan:
        """从冻结 Requirement 生成首轮计划；不读取用户原文，也不执行工具。"""

        if requirement.initial_plan is not None:
            return requirement.initial_plan
        return _build_initial_plan(requirement)


def _build_initial_plan(
    requirement_without_plan: ResearchAgentRequirement,
) -> ResearchInitialPlan:
    """根据冻结的证据需求生成首轮可执行节点。"""

    requirement = requirement_without_plan
    roles = tuple(item.role for item in requirement.time_bindings)
    comparison = _comparison_for(roles)
    nodes: list[ResearchInitialPlanNode] = []

    premise = requirement.premise_to_verify
    premise_node_id: str | None = None
    if premise is not None:
        premise_node_id = "premise-confirmation"
        nodes.append(
            ResearchInitialPlanNode(
                node_id=premise_node_id,
                node_type="query",
                batch_index=0,
                metrics=(premise.metric_ref,),
                time_ranges=premise.time_roles,
                filters=requirement.immutable_filters,
                comparison=_comparison_for(premise.time_roles),
                analysis="compare",
                purpose=(
                    f"前提确认：{premise.statement or premise.metric_ref}"
                )[:1000],
            )
        )

    # 前提查询本身已经覆盖同一目标指标和同一时间窗口时，直接复用该
    # Evidence，避免首轮再次执行内容相同的总量查询。
    target_node_id = "target-comparison"
    premise_covers_target = (
        premise is not None
        and tuple(requirement.target_metric_refs) == (premise.metric_ref,)
        and set(premise.time_roles) == set(roles)
    )
    if premise_covers_target:
        target_node_id = premise_node_id or target_node_id
    else:
        nodes.append(
            ResearchInitialPlanNode(
                node_id=target_node_id,
                node_type="query",
                batch_index=1,
                metrics=requirement.target_metric_refs,
                time_ranges=roles,
                filters=requirement.immutable_filters,
                comparison=comparison,
                analysis="compare" if comparison is not ResearchQueryComparison.NONE else "exploration",
                purpose="确认目标指标在冻结时间范围内的基础变化",
            )
        )

    explicit_dimensions = _required_dimensions(requirement.evidence_requirements)
    dimension_node_ids: dict[str, str] = {}
    for index, dimension_ref in enumerate(explicit_dimensions, start=1):
        node_id = f"dimension-analysis-{index}"
        dimension_node_ids[dimension_ref] = node_id
        nodes.append(
            ResearchInitialPlanNode(
                node_id=node_id,
                node_type="query",
                batch_index=1,
                metrics=requirement.target_metric_refs,
                dimensions=(dimension_ref,),
                time_ranges=roles,
                filters=requirement.immutable_filters,
                comparison=comparison,
                analysis="breakdown",
                purpose=f"按明确维度 {dimension_ref} 分解目标指标变化",
            )
        )

    for index, metric_ref in enumerate(
        _required_drivers(
            requirement.evidence_requirements,
            requirement.scope.driver_metric_refs,
        ),
        start=1,
    ):
        nodes.append(
            ResearchInitialPlanNode(
                node_id=f"driver-analysis-{index}",
                node_type="query",
                batch_index=1,
                metrics=(metric_ref,),
                time_ranges=roles,
                filters=requirement.immutable_filters,
                comparison=comparison,
                analysis="compare" if comparison is not ResearchQueryComparison.NONE else "exploration",
                purpose=f"确认明确要求的驱动指标 {metric_ref}",
            )
        )

    # 只有冻结 Requirement 明确要求 reconciliation 时，才生成贡献计算。
    # Scope 中存在可贡献维度只表示能力范围，不能据此推断用户要求了贡献分析。
    contribution_requested = any(
        item.kind == "reconciliation" for item in requirement.evidence_requirements
    )
    if contribution_requested:
        contribution_dimensions = tuple(
            ref
            for ref in requirement.scope.contribution_dimension_refs
            if ref in explicit_dimensions
        )
        for index, dimension_ref in enumerate(contribution_dimensions, start=1):
            breakdown_node_id = dimension_node_ids.get(dimension_ref)
            if breakdown_node_id is None:
                continue
            nodes.append(
                ResearchInitialPlanNode(
                    node_id=f"contribution-{index}",
                    node_type="compute",
                    batch_index=2,
                    dependency_node_ids=(breakdown_node_id, target_node_id),
                    metrics=(requirement.target_metric_refs[0],),
                    dimensions=(dimension_ref,),
                    compute_operation=ResearchComputeOperation.CONTRIBUTION,
                    tolerance=requirement.scope.contribution_tolerance,
                    purpose=f"计算 {dimension_ref} 对目标指标变化的贡献并完成总量对账",
                )
            )

    return ResearchInitialPlan(nodes=tuple(nodes))


def _comparison_for(roles: Iterable[ResearchTimeRole]) -> ResearchQueryComparison:
    role_set = set(roles)
    if {ResearchTimeRole.CURRENT, ResearchTimeRole.PREVIOUS} <= role_set:
        return ResearchQueryComparison.DIFFERENCE
    return ResearchQueryComparison.NONE


def _required_dimensions(
    requirements: tuple[ResearchEvidenceRequirement, ...],
) -> tuple[str, ...]:
    refs: list[str] = []
    for item in requirements:
        if item.kind != "dimension_or_driver_analysis":
            continue
        for ref in item.required_asset_refs:
            if ref.startswith("DIMENSION:") and ref not in refs:
                refs.append(ref)
    return tuple(refs)


def _required_drivers(
    requirements: tuple[ResearchEvidenceRequirement, ...],
    allowed_driver_refs: tuple[str, ...],
) -> tuple[str, ...]:
    """只提取冻结 Scope 已确认的驱动指标，避免把目标指标重复规划为驱动查询。"""

    allowed = set(allowed_driver_refs)
    refs: list[str] = []
    for item in requirements:
        if item.kind not in {"claim_support", "dimension_or_driver_analysis"}:
            continue
        for ref in item.required_asset_refs:
            if ref in allowed and ref not in refs:
                refs.append(ref)
    return tuple(refs)


def build_initial_plan(
    requirement: ResearchAgentRequirement,
) -> ResearchInitialPlan:
    """兼容旧调用的首轮 Planner 入口。"""

    return ResearchInitialPlanner().plan(requirement)


__all__ = ["ResearchInitialPlanner", "build_initial_plan"]
