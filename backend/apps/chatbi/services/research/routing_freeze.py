"""路由期冻结 Research Agent 输入：从已绑定语义和治理契约直接构造新契约。

阶段 8（doc38 §12）删除旧 Action 契约后，路由期不再经过旧
``ResearchRequirement`` 中转——本模块把原冻结逻辑与新契约构造熔合为
一步：治理范围（维度/驱动关系/层级/时间绑定）的推导规则原样保留，
产出直接是 ``ResearchAgentRequirement``。不重新检索，也不替模型选择
执行方向。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from apps.chatbi.errors import ResearchRequirementError
from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchBudget,
    ResearchDriverRelationship,
    ResearchEvidenceRequirement,
    ResearchHierarchy,
    ResearchImmutableFilter,
    ResearchPremise,
    ResearchScope,
    ResearchTimeBinding,
    ResearchTimeRole,
    ResearchVersionSnapshot,
)
from apps.chatbi.models.dto.semantic_parse import SemanticParseOutput
from apps.semantic import DatasetSchema
from apps.semantic.models.dto import (
    DimensionHierarchyRuntimeDTO,
    MetricRelationshipRuntimeDTO,
)
from apps.temporal import TemporalContext, resolve_time_range

_MAX_SCOPE_DIMENSIONS = 20
_MAX_SCOPE_DRIVER_METRICS = 10


class _ResearchSchemaElement(Protocol):
    """范围投影只读取语义层公开 Schema 元素的稳定字段。"""

    id: int
    model: int | None
    ext_info: dict[str, Any]


def freeze_research_requirement(
    *,
    semantic_parse: SemanticParseOutput,
    schema: DatasetSchema,
    temporal_context: TemporalContext | None,
    budget: ResearchBudget | None = None,
    tenant_scope: str,
    dataset_ref: str,
    user_id: int | None = None,
    datasource_id: int | None = None,
    permission_version: str | None = None,
    authorized_tables: tuple[str, ...] = (),
) -> ResearchAgentRequirement:
    """冻结新契约 Requirement；run_id 由主路径适配层按 Run 身份补盖。"""

    dynamic = semantic_parse.multi_step
    if dynamic is None or dynamic.type != "dynamic_research":
        raise ResearchRequirementError(ResearchRequirementError.TARGET_METRIC_REQUIRED)
    selected_metric_refs = tuple(
        dict.fromkeys(item.ref for item in semantic_parse.measures)
    )
    if not selected_metric_refs:
        raise ResearchRequirementError(ResearchRequirementError.TARGET_METRIC_REQUIRED)

    # dynamic_research 的 required_driver_metric_refs 是解析阶段已经明确的
    # 目标/驱动边界，必须先完成分类再投影治理关系。否则派生驱动指标自身的
    # 公式关系会被误当成目标关系，污染本次 Research Scope。
    declared_driver_refs = tuple(
        dict.fromkeys(dynamic.required_driver_metric_refs)
    )
    declared_driver_set = set(declared_driver_refs)
    target_metric_refs = tuple(
        ref for ref in selected_metric_refs if ref not in declared_driver_set
    )
    if not target_metric_refs:
        raise ResearchRequirementError(ResearchRequirementError.TARGET_METRIC_REQUIRED)

    metrics = {
        _metric_ref(item): item for item in schema.metrics if item.model is not None
    }
    dimensions = {
        _dimension_ref(item): item
        for item in schema.dimensions
        if item.model is not None
    }
    if any(ref not in metrics for ref in declared_driver_refs):
        raise ResearchRequirementError(
            ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
        )
    if (
        dynamic.premise_to_verify is not None
        and dynamic.premise_to_verify.metric_ref in declared_driver_set
    ):
        raise ResearchRequirementError(
            ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
        )
    try:
        target_metrics = [metrics[ref] for ref in target_metric_refs]
    except KeyError as exc:
        raise ResearchRequirementError(
            ResearchRequirementError.TARGET_METRIC_NOT_FOUND
        ) from exc
    target_model_ids = {item.model for item in target_metrics}
    if len(target_model_ids) != 1:
        raise ResearchRequirementError(
            ResearchRequirementError.TARGET_METRIC_SINGLE_MODEL_REQUIRED
        )
    target_model_id = next(iter(target_model_ids))
    if target_model_id is None:
        raise ResearchRequirementError(
            ResearchRequirementError.TARGET_METRIC_SINGLE_MODEL_REQUIRED
        )

    explicit_dimension_refs = tuple(
        dict.fromkeys(
            (
                *(item.ref for item in semantic_parse.group_by),
                *dynamic.required_dimension_refs,
            )
        )
    )
    for ref in explicit_dimension_refs:
        dimension = dimensions.get(ref)
        if dimension is None:
            raise ResearchRequirementError(ResearchRequirementError.DIMENSION_NOT_FOUND)
        if dimension.model != target_model_id:
            raise ResearchRequirementError(
                ResearchRequirementError.DIMENSION_MODEL_MISMATCH
            )

    governed_dimensions, filter_dimensions, contribution_dimensions = (
        _governed_dimensions(
            schema,
            target_metrics,
            dimensions,
        )
    )
    time_roles, time_bindings = _time_bindings(
        semantic_parse,
        schema,
        target_model_id,
        temporal_context,
    )
    hierarchies = _governed_hierarchies(
        schema,
        dimensions,
        target_model_id,
        candidate_dimension_refs={*explicit_dimension_refs, *governed_dimensions},
    )
    driver_dimension_refs = _cross_model_driver_dimension_refs(
        schema,
        target_metric_refs,
        metrics,
    )
    scope_dimension_refs = tuple(
        dict.fromkeys(
            (
                *explicit_dimension_refs,
                *governed_dimensions,
                *driver_dimension_refs,
                *(ref for hierarchy in hierarchies for ref in hierarchy.dimension_refs),
            )
        )
    )[:_MAX_SCOPE_DIMENSIONS]
    hierarchies = tuple(
        item
        for item in hierarchies
        if set(item.dimension_refs) <= set(scope_dimension_refs)
    )
    allowed_filter_refs = tuple(
        ref
        for ref in scope_dimension_refs
        if ref in set(explicit_dimension_refs) | set(filter_dimensions)
    )
    driver_relationships = _driver_relationships(
        schema=schema,
        target_metrics=target_metrics,
        metrics=metrics,
        dimension_refs=scope_dimension_refs,
        time_roles=time_roles,
    )
    governed_driver_refs = {
        metric_ref
        for item in driver_relationships
        for metric_ref in (item.component_metric_refs or (item.driver_metric_ref,))
    }
    if not declared_driver_set <= governed_driver_refs:
        raise ResearchRequirementError(
            ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
        )
    time_bindings_by_model = _time_bindings_by_model(
        schema=schema,
        target_model_id=target_model_id,
        driver_relationships=driver_relationships,
        time_roles=time_roles,
        target_bindings=time_bindings,
        temporal_context=temporal_context,
    )
    driver_metric_refs = tuple(
        dict.fromkeys(
            (
                *_driver_metric_refs(target_metrics, metrics),
                *(
                    metric_ref
                    for item in driver_relationships
                    for metric_ref in (
                        item.component_metric_refs or (item.driver_metric_ref,)
                    )
                ),
            )
        )
    )[:_MAX_SCOPE_DRIVER_METRICS]
    allowed_driver_refs = set(driver_metric_refs)
    driver_relationships = tuple(
        item
        for item in driver_relationships
        if set(item.component_metric_refs or (item.driver_metric_ref,))
        <= allowed_driver_refs
    )
    if not scope_dimension_refs and not driver_metric_refs:
        raise ResearchRequirementError(ResearchRequirementError.SCOPE_EMPTY)
    # 新契约要求时间绑定的维度必须在 Scope 内；旧冻结把默认时间维度单独
    # 挂在 time_bindings 上而不列入 scope.dimension_refs，这里做确定性并集
    # （原 shadow 投影器同口径，阶段 8 融合进路由冻结）。
    scope_dimension_refs = tuple(
        dict.fromkeys(
            (
                *scope_dimension_refs,
                *(binding.dimension_ref for binding in time_bindings),
                *(
                    binding.dimension_ref
                    for bindings in time_bindings_by_model.values()
                    for binding in bindings
                ),
            )
        )
    )

    included_refs = {
        *target_metric_refs,
        *scope_dimension_refs,
        *driver_metric_refs,
    }
    eligible_refs = {
        *(
            _dimension_ref(item)
            for item in schema.dimensions
            if not _is_time_dimension(item)
        ),
        *(
            _metric_ref(item)
            for item in schema.metrics
            if _metric_ref(item) in {*target_metric_refs, *driver_metric_refs}
        ),
    }
    excluded_asset_refs = tuple(sorted(eligible_refs - included_refs))
    immutable_filters = tuple(
        ResearchImmutableFilter(
            target_ref=item.target_ref,
            operator=item.operator,
            value=item.value,
        )
        for item in semantic_parse.filters
    )
    contribution_dimension_refs = tuple(
        item for item in scope_dimension_refs if item in set(contribution_dimensions)
    )
    contribution_metric_refs = (
        target_metric_refs
        if contribution_dimension_refs
        and set(time_roles) == {"current", "previous"}
        and target_metrics
        and all(
            _metric_additivity(schema, item.id) == "FULL" for item in target_metrics
        )
        else ()
    )
    contribution_tolerance = _contribution_tolerance(
        schema,
        target_metrics,
        contribution_dimension_refs,
    )
    explicit_driver_refs = declared_driver_refs
    contribution_requested = any(
        item.type.value == "contribution"
        for item in semantic_parse.calculations
    )
    required_contribution_dimensions = (
        tuple(
            ref
            for ref in explicit_dimension_refs
            if ref in set(contribution_dimension_refs)
        )
        if contribution_requested
        else ()
    )
    # Scope 先以原始字段集合参与指纹，再补齐身份三要素（tenant/dataset/
    # 指纹）装配——指纹不能包含它自己的值。旧契约 payload 中的
    # allowed_actions 随阶段 8 删除；治理语义仍由 required_* 集合锚定。
    if not schema.schema_fingerprint:
        raise ResearchRequirementError(
            ResearchRequirementError.SCHEMA_FINGERPRINT_REQUIRED
        )
    scope_fields: dict[str, Any] = {
        "target_metric_refs": target_metric_refs,
        "dimension_refs": scope_dimension_refs,
        "driver_metric_refs": driver_metric_refs,
        "hierarchies": hierarchies,
        "driver_relationships": driver_relationships,
        "allowed_filter_refs": allowed_filter_refs,
        "contribution_metric_refs": contribution_metric_refs,
        "contribution_dimension_refs": contribution_dimension_refs,
        "contribution_tolerance": contribution_tolerance,
        "excluded_asset_refs": excluded_asset_refs,
    }
    scope_fingerprint = _fingerprint(
        {
            "goal": dynamic.goal,
            "reason": dynamic.reason,
            "target_metric_refs": target_metric_refs,
            "time_roles": time_roles,
            "time_bindings": [item.model_dump(mode="json") for item in time_bindings],
            "time_bindings_by_model": {
                model_id: [item.model_dump(mode="json") for item in bindings]
                for model_id, bindings in time_bindings_by_model.items()
            },
            "immutable_filters": [
                item.model_dump(mode="json") for item in immutable_filters
            ],
            "scope": {
                key: (
                    [item.model_dump(mode="json") for item in value]
                    if isinstance(value, tuple)
                    and value
                    and hasattr(value[0], "model_dump")
                    else value
                )
                for key, value in scope_fields.items()
            },
            "required_dimension_refs": explicit_dimension_refs,
            "required_driver_metric_refs": explicit_driver_refs,
            "required_contribution_dimension_refs": (
                required_contribution_dimensions
            ),
            "schema_version": schema.schema_version,
            "contract_version": schema.contract_version,
            "schema_fingerprint": schema.schema_fingerprint,
        }
    )
    permission_fingerprint = research_permission_fingerprint(
        schema_fingerprint=schema.schema_fingerprint,
        scope_fingerprint=scope_fingerprint,
        tenant_scope=tenant_scope,
        dataset_ref=dataset_ref,
        user_id=user_id,
        datasource_id=datasource_id,
        permission_version=permission_version,
        authorized_tables=authorized_tables,
    )
    premise = (
        ResearchPremise.model_validate(dynamic.premise_to_verify.model_dump())
        if dynamic.premise_to_verify is not None
        else None
    )
    evidence_requirements: list[ResearchEvidenceRequirement] = []
    if premise is not None:
        evidence_requirements.append(
            ResearchEvidenceRequirement(
                requirement_id="premise-confirmation",
                kind="premise_confirmation",
                description="确认用户陈述的指标事实是否成立",
                required_asset_refs=(premise.metric_ref,),
            )
        )
    evidence_requirements.append(
        ResearchEvidenceRequirement(
            requirement_id="target-analysis",
            kind="dimension_or_driver_analysis",
            description="围绕目标指标完成归因分析",
            required_asset_refs=target_metric_refs,
        )
    )
    for index, ref in enumerate(explicit_dimension_refs, start=1):
        evidence_requirements.append(
            ResearchEvidenceRequirement(
                requirement_id=f"required-dimension-{index}",
                kind="dimension_or_driver_analysis",
                description=f"覆盖用户明确要求的分析维度 {ref}",
                required_asset_refs=(ref,),
            )
        )
    for index, ref in enumerate(explicit_driver_refs, start=1):
        evidence_requirements.append(
            ResearchEvidenceRequirement(
                requirement_id=f"required-driver-{index}",
                kind="claim_support",
                description=f"验证用户明确要求的驱动指标 {ref}",
                required_asset_refs=(ref,),
            )
        )
    if contribution_requested:
        evidence_requirements.append(
            ResearchEvidenceRequirement(
                requirement_id="contribution-reconciliation",
                kind="reconciliation",
                description="完成贡献度分解并与总量变化对账",
                required_asset_refs=tuple(
                    dict.fromkeys(
                        (*target_metric_refs, *required_contribution_dimensions)
                    )
                ),
            )
        )

    return ResearchAgentRequirement(
        run_id="routing",  # 占位：主路径适配层按 Run 身份覆盖。
        goal=dynamic.goal,
        reason=dynamic.reason,
        target_metric_refs=target_metric_refs,
        premise_to_verify=premise,
        time_bindings=time_bindings,
        time_bindings_by_model=time_bindings_by_model,
        immutable_filters=immutable_filters,
        scope=ResearchScope(
            **scope_fields,
            tenant_scope=tenant_scope,
            dataset_ref=dataset_ref,
            scope_fingerprint=scope_fingerprint,
        ),
        evidence_requirements=tuple(evidence_requirements),
        budget=budget or ResearchBudget(),
        version_snapshot=ResearchVersionSnapshot(
            schema_version=schema.schema_version,
            contract_version=schema.contract_version,
            schema_fingerprint=schema.schema_fingerprint,
            scope_fingerprint=scope_fingerprint,
            permission_fingerprint=permission_fingerprint,
        ),
    )


def _governed_dimensions(
    schema: DatasetSchema,
    target_metrics: Sequence[_ResearchSchemaElement],
    dimensions: Mapping[str, _ResearchSchemaElement],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """只投影指标能力契约明确允许的物理维度。"""

    metric_ids = {item.id for item in target_metrics}
    dimension_by_id = {item.id: item for item in dimensions.values()}
    group_refs: list[str] = []
    filter_refs: list[str] = []
    contribution_refs: list[str] = []
    capabilities = sorted(
        (
            item
            for item in schema.metric_dimension_capabilities
            if isinstance(item, dict)
            and item.get("metric_id") in metric_ids
            and str(item.get("aggregation_safety") or "").upper()
            in {"SAFE", "PRE_AGGREGATE_REQUIRED"}
        ),
        key=lambda item: (
            int(item.get("physical_dimension_id") or 0),
            int(item.get("id") or 0),
        ),
    )
    for capability in capabilities:
        physical_id = capability.get("physical_dimension_id")
        if not isinstance(physical_id, int):
            continue
        dimension = dimension_by_id.get(physical_id)
        if dimension is None or _is_time_dimension(dimension):
            continue
        ref = _dimension_ref(dimension)
        usages = {str(item).upper() for item in capability.get("usages") or [] if item}
        if "GROUP_BY" in usages:
            group_refs.append(ref)
        if "FILTER" in usages:
            filter_refs.append(ref)
        if "CONTRIBUTION" in usages:
            contribution_refs.append(ref)
    return (
        tuple(dict.fromkeys(group_refs)),
        tuple(dict.fromkeys(filter_refs)),
        tuple(dict.fromkeys(contribution_refs)),
    )


def _driver_metric_refs(
    target_metrics: Sequence[_ResearchSchemaElement],
    metrics: Mapping[str, _ResearchSchemaElement],
) -> tuple[str, ...]:
    """派生指标的显式 metric_refs 是第一阶段唯一驱动指标来源。"""

    by_id = {item.id: ref for ref, item in metrics.items()}
    target_refs = {_metric_ref(item) for item in target_metrics}
    refs: list[str] = []
    for metric in target_metrics:
        formula = metric.ext_info.get("formula_definition")
        raw_refs = (
            [
                item.get("metric_id")
                for item in formula.get("components") or []
                if isinstance(item, dict)
            ]
            if isinstance(formula, dict)
            else metric.ext_info.get("metric_refs")
        )
        for metric_id in raw_refs if isinstance(raw_refs, list) else []:
            if (
                isinstance(metric_id, int)
                and metric_id in by_id
                and by_id[metric_id] not in target_refs
            ):
                refs.append(by_id[metric_id])
    return tuple(dict.fromkeys(refs))


def _cross_model_driver_dimension_refs(
    schema: DatasetSchema,
    target_metric_refs: tuple[str, ...],
    metrics: Mapping[str, _ResearchSchemaElement],
) -> tuple[str, ...]:
    """把跨模型关系两侧的共同维度纳入封闭 Research Scope。"""

    result: list[str] = []
    target_refs = set(target_metric_refs)
    for raw in schema.research_relationships:
        if not isinstance(raw, MetricRelationshipRuntimeDTO):
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        target = metrics.get(raw.target_metric_ref)
        driver = metrics.get(raw.driver_metric_ref)
        if (
            target is None
            or driver is None
            or raw.target_metric_ref not in target_refs
            or target.model == driver.model
            or raw.relationship_type
            not in {"certified_driver", "governed_analysis_relation"}
        ):
            continue
        refs = (
            *raw.dimension_refs,
            *tuple(
                ref
                for model_refs in raw.dimension_refs_by_model.values()
                for ref in model_refs
            ),
        )
        for ref in refs:
            if ref not in result:
                result.append(ref)
    return tuple(result)


def _governed_hierarchies(
    schema: DatasetSchema,
    dimensions: Mapping[str, _ResearchSchemaElement],
    target_model_id: int,
    *,
    candidate_dimension_refs: set[str],
) -> tuple[ResearchHierarchy, ...]:
    """只接受 Schema 中标记为 CERTIFIED 且可安全查询的相邻层级。"""

    result: list[ResearchHierarchy] = []
    for raw in schema.dimension_hierarchies:
        if not isinstance(raw, DimensionHierarchyRuntimeDTO):
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        refs = raw.dimension_refs_by_model.get(str(target_model_id))
        if not refs or len(refs) < 2:
            continue
        normalized_refs = tuple(refs)
        if len(normalized_refs) != len(set(normalized_refs)):
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        for ref in normalized_refs:
            dimension = dimensions.get(ref)
            if dimension is None or dimension.model != target_model_id:
                raise ResearchRequirementError(
                    ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
                )
        # 层级契约可以属于数据集中的其他指标；对当前目标不可用时不授权即可。
        if not set(normalized_refs) <= candidate_dimension_refs:
            continue
        result.append(
            ResearchHierarchy(hierarchy_id=str(raw.id), dimension_refs=normalized_refs)
        )
    return tuple(result)


def _driver_relationships(
    *,
    schema: DatasetSchema,
    target_metrics: Sequence[_ResearchSchemaElement],
    metrics: Mapping[str, _ResearchSchemaElement],
    dimension_refs: tuple[str, ...],
    time_roles: tuple[str, ...],
) -> tuple[ResearchDriverRelationship, ...]:
    """把公式组成和已认证分析关系统一成 Research 的驱动关系。"""

    target_refs = {_metric_ref(item) for item in target_metrics}
    metric_by_ref = dict(metrics)
    target_model_id = next(iter({item.model for item in target_metrics}), None)
    relationships: list[ResearchDriverRelationship] = []
    for raw in schema.research_relationships:
        if not isinstance(raw, MetricRelationshipRuntimeDTO):
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        target_ref = raw.target_metric_ref
        driver_ref = raw.driver_metric_ref
        component_refs = raw.component_metric_refs or (driver_ref,)
        if target_ref not in target_refs:
            continue
        if (
            not isinstance(driver_ref, str)
            or driver_ref not in metric_by_ref
            or any(ref not in metric_by_ref for ref in component_refs)
        ):
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        driver_metric = metric_by_ref[driver_ref]
        if driver_metric.model != target_model_id and raw.relationship_type not in {
            "certified_driver",
            "governed_analysis_relation",
        }:
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        relationship_type = raw.relationship_type
        if relationship_type not in {
            "formula_component",
            "certified_driver",
            "governed_analysis_relation",
        }:
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        normalized_dimensions = tuple(
            dict.fromkeys(
                (
                    *raw.dimension_refs,
                    *(
                        ref
                        for model_refs in raw.dimension_refs_by_model.values()
                        for ref in model_refs
                    ),
                )
            )
        )
        normalized_time_roles = tuple(dict.fromkeys(raw.time_roles))
        fingerprint = raw.relationship_fingerprint
        if relationship_type == "formula_component" and normalized_time_roles == (
            "single",
        ):
            # 公式关系本身不限制比较时段；单时点是 Schema 中的默认标记。
            normalized_time_roles = time_roles
        if (
            any(not isinstance(item, str) for item in normalized_dimensions)
            or any(not isinstance(item, str) for item in normalized_time_roles)
            or not set(normalized_dimensions) <= set(dimension_refs)
            or not set(normalized_time_roles) <= set(time_roles)
        ):
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        relationships.append(
            ResearchDriverRelationship(
                target_metric_ref=target_ref,
                driver_metric_ref=driver_ref,
                component_metric_refs=component_refs
                if relationship_type == "formula_component"
                else (),
                relationship_type=relationship_type,
                validation_method=raw.validation_method,
                expected_direction=raw.expected_direction,
                dimension_refs_by_model=dict(raw.dimension_refs_by_model),
                relation_path=raw.relation_path,
                formula_definition=(
                    metric_by_ref[target_ref].ext_info.get("formula_definition")
                    if relationship_type == "formula_component"
                    and isinstance(
                        metric_by_ref[target_ref].ext_info.get("formula_definition"),
                        dict,
                    )
                    else None
                ),
                dimension_refs=normalized_dimensions,
                time_roles=tuple(
                    ResearchTimeRole(role) for role in normalized_time_roles
                ),
                relationship_fingerprint=fingerprint,
            )
        )
    unique: dict[str, ResearchDriverRelationship] = {}
    for relationship in relationships:
        unique[relationship.relationship_fingerprint] = relationship
    return tuple(unique.values())


def _formula_relationship_dimensions(
    schema: DatasetSchema,
    *,
    target_metric_id: int,
    driver_metric_id: int,
    scope_dimension_refs: tuple[str, ...],
) -> tuple[str, ...]:
    """公式组成关系只授权目标与驱动指标共同支持的分组维度。"""

    dimension_ref_by_id = {
        item.id: _dimension_ref(item)
        for item in schema.dimensions
        if item.model is not None
    }
    supported_by_metric: dict[int, set[str]] = {
        target_metric_id: set(),
        driver_metric_id: set(),
    }
    for capability in schema.metric_dimension_capabilities:
        if not isinstance(capability, dict):
            continue
        metric_id = capability.get("metric_id")
        if metric_id not in supported_by_metric:
            continue
        if str(capability.get("aggregation_safety") or "").upper() not in {
            "SAFE",
            "PRE_AGGREGATE_REQUIRED",
        }:
            continue
        usages = {str(item).upper() for item in capability.get("usages") or []}
        if "GROUP_BY" not in usages:
            continue
        physical_id = capability.get("physical_dimension_id")
        if isinstance(physical_id, int) and physical_id in dimension_ref_by_id:
            supported_by_metric[metric_id].add(dimension_ref_by_id[physical_id])
    common = (
        supported_by_metric[target_metric_id] & supported_by_metric[driver_metric_id]
    )
    return tuple(ref for ref in scope_dimension_refs if ref in common)


def _time_bindings(
    semantic_parse: SemanticParseOutput,
    schema: DatasetSchema,
    model_id: int,
    temporal_context: TemporalContext | None,
) -> tuple[tuple[ResearchTimeRole, ...], tuple[ResearchTimeBinding, ...]]:
    if not semantic_parse.time_filters:
        return (ResearchTimeRole.SINGLE,), ()
    roles = tuple(ResearchTimeRole(item.role) for item in semantic_parse.time_filters)
    if len(roles) != len(set(roles)):
        raise ResearchRequirementError(ResearchRequirementError.TIME_ROLE_DUPLICATED)
    if temporal_context is None:
        raise ResearchRequirementError(ResearchRequirementError.TIME_CONTEXT_REQUIRED)
    time_dimension = next(
        (
            item
            for item in schema.dimensions
            if item.model == model_id and _is_time_dimension(item)
        ),
        None,
    )
    if time_dimension is None:
        raise ResearchRequirementError(ResearchRequirementError.TIME_DIMENSION_REQUIRED)
    bindings: list[ResearchTimeBinding] = []
    for item in semantic_parse.time_filters:
        normalized = resolve_time_range(item.expression, temporal_context)
        if not isinstance(normalized, dict) or normalized.get("kind") == "unsupported":
            raise ResearchRequirementError(
                ResearchRequirementError.TIME_RANGE_UNRESOLVED
            )
        bindings.append(
            ResearchTimeBinding(
                role=ResearchTimeRole(item.role),
                expression=item.expression,
                dimension_ref=_dimension_ref(time_dimension),
                normalized=normalized,
            )
        )
    return roles, tuple(bindings)


def _time_bindings_by_model(
    *,
    schema: DatasetSchema,
    target_model_id: int,
    driver_relationships: Sequence[ResearchDriverRelationship],
    time_roles: tuple[ResearchTimeRole, ...],
    target_bindings: tuple[ResearchTimeBinding, ...],
    temporal_context: TemporalContext | None,
) -> dict[str, tuple[ResearchTimeBinding, ...]]:
    """为跨模型驱动查询分别解析各模型的物理时间维度。"""

    if time_roles == (ResearchTimeRole.SINGLE,) or not driver_relationships:
        return {}
    model_ids = {target_model_id}
    for relationship in driver_relationships:
        driver_model = _asset_model_id(relationship.driver_metric_ref)
        if driver_model is not None and driver_model != target_model_id:
            model_ids.add(driver_model)
    if len(model_ids) == 1:
        return {}
    if temporal_context is None:
        raise ResearchRequirementError(ResearchRequirementError.TIME_CONTEXT_REQUIRED)
    result: dict[str, tuple[ResearchTimeBinding, ...]] = {
        str(target_model_id): target_bindings
    }
    for model_id in sorted(model_ids - {target_model_id}):
        time_dimension = next(
            (
                item
                for item in schema.dimensions
                if item.model == model_id and _is_time_dimension(item)
            ),
            None,
        )
        if time_dimension is None:
            raise ResearchRequirementError(
                ResearchRequirementError.TIME_DIMENSION_REQUIRED
            )
        bindings: list[ResearchTimeBinding] = []
        for role in time_roles:
            source = next(
                (item for item in target_bindings if item.role == role),
                None,
            )
            if source is None:
                raise ResearchRequirementError(
                    ResearchRequirementError.TIME_RANGE_UNRESOLVED
                )
            normalized = resolve_time_range(source.expression, temporal_context)
            if (
                not isinstance(normalized, dict)
                or normalized.get("kind") == "unsupported"
            ):
                raise ResearchRequirementError(
                    ResearchRequirementError.TIME_RANGE_UNRESOLVED
                )
            bindings.append(
                ResearchTimeBinding(
                    role=ResearchTimeRole(role),
                    expression=source.expression,
                    dimension_ref=_dimension_ref(time_dimension),
                    normalized=normalized,
                )
            )
        result[str(model_id)] = tuple(bindings)
    return result


def _metric_additivity(schema: DatasetSchema, metric_id: int) -> str | None:
    contract = next(
        (
            item
            for item in schema.metric_contracts
            if isinstance(item, dict) and item.get("metric_id") == metric_id
        ),
        None,
    )
    value = contract.get("additivity") if contract is not None else None
    return str(value).upper() if value else None


def _contribution_tolerance(
    schema: DatasetSchema,
    target_metrics: Sequence[_ResearchSchemaElement],
    dimension_refs: tuple[str, ...],
) -> float:
    """读取服务端配置的贡献度对账容差，不接受用户或模型覆盖。"""

    values: list[float] = []
    dimension_ids = {
        int(ref.split(":", 2)[1])
        for ref in dimension_refs
        if ref.startswith("DIMENSION:")
    }
    metric_ids = {item.id for item in target_metrics}
    for capability in schema.metric_dimension_capabilities:
        if not isinstance(capability, dict):
            continue
        if capability.get("metric_id") not in metric_ids:
            continue
        if capability.get("physical_dimension_id") not in dimension_ids:
            continue
        raw = capability.get("contribution_tolerance", 1e-6)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 0:
            values.append(float(raw))
    return max(values, default=1e-6)


def _is_time_dimension(dimension: _ResearchSchemaElement) -> bool:
    """按发布契约识别全部时间维度，不只排除默认时间维度。"""

    ext_info = dimension.ext_info
    dimension_type = str(ext_info.get("dimension_type") or "").lower()
    data_type = str(ext_info.get("dimension_data_type") or "").lower()
    return bool(
        ext_info.get("is_default_time")
        or dimension_type in {"partition_time", "time", "datetime"}
        or data_type in {"date", "datetime", "timestamp", "time"}
    )


def _metric_ref(metric: _ResearchSchemaElement) -> str:
    return f"METRIC:{metric.id}:{metric.model}"


def _dimension_ref(dimension: _ResearchSchemaElement) -> str:
    return f"DIMENSION:{dimension.id}:{dimension.model}"


def _asset_model_id(ref: str) -> int | None:
    """从已校验资产引用读取模型 ID。"""

    parts = ref.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return None
    return int(parts[2])


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def research_permission_fingerprint(
    *,
    schema_fingerprint: str,
    scope_fingerprint: str,
    tenant_scope: str,
    dataset_ref: str,
    user_id: int | None,
    datasource_id: int | None,
    permission_version: str | None,
    authorized_tables: tuple[str, ...],
) -> str:
    """对真实执行身份和授权表集合生成稳定权限指纹。"""

    return _fingerprint(
        {
            "schema_fingerprint": schema_fingerprint,
            "scope_fingerprint": scope_fingerprint,
            "tenant_scope": tenant_scope,
            "dataset_ref": dataset_ref,
            "user_id": user_id,
            "datasource_id": datasource_id,
            "permission_version": permission_version,
            "authorized_tables": sorted(
                {
                    str(table).strip().lower()
                    for table in authorized_tables
                    if str(table).strip()
                }
            ),
        }
    )[:32]


__all__ = ["freeze_research_requirement", "research_permission_fingerprint"]
