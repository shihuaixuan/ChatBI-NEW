"""根据已绑定语义和治理契约构建不可扩大的 Research 范围。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from apps.chatbi.errors import ResearchRequirementError
from apps.chatbi.models.dto.research import (
    ResearchActionType,
    ResearchBudget,
    ResearchDriverRelationship,
    ResearchFilterBinding,
    ResearchHierarchy,
    ResearchReason,
    ResearchRequirement,
    ResearchScope,
    ResearchTimeBinding,
    ResearchVersionSnapshot,
)
from apps.chatbi.models.dto.semantic_parse import SemanticParseOutput
from apps.semantic import DatasetSchema
from apps.temporal import TemporalContext, resolve_time_range

_MAX_SCOPE_DIMENSIONS = 20
_MAX_SCOPE_DRIVER_METRICS = 10


class _ResearchSchemaElement(Protocol):
    """范围投影只读取语义层公开 Schema 元素的稳定字段。"""

    id: int
    model: int | None
    ext_info: dict[str, Any]


def build_research_requirement(
    *,
    semantic_parse: SemanticParseOutput,
    schema: DatasetSchema,
    temporal_context: TemporalContext | None,
    budget: ResearchBudget | None = None,
) -> ResearchRequirement:
    """构建 ResearchRequirement；不重新检索，也不替模型选择执行方向。"""

    dynamic = semantic_parse.multi_step
    if dynamic is None or dynamic.type != "dynamic_research":
        raise ResearchRequirementError(
            ResearchRequirementError.TARGET_METRIC_REQUIRED
        )
    target_metric_refs = tuple(dict.fromkeys(item.ref for item in semantic_parse.measures))
    if not target_metric_refs:
        raise ResearchRequirementError(
            ResearchRequirementError.TARGET_METRIC_REQUIRED
        )

    metrics = {_metric_ref(item): item for item in schema.metrics if item.model is not None}
    dimensions = {
        _dimension_ref(item): item
        for item in schema.dimensions
        if item.model is not None
    }
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
        dict.fromkeys(item.ref for item in semantic_parse.group_by)
    )
    for ref in explicit_dimension_refs:
        dimension = dimensions.get(ref)
        if dimension is None:
            raise ResearchRequirementError(
                ResearchRequirementError.DIMENSION_NOT_FOUND
            )
        if dimension.model != target_model_id:
            raise ResearchRequirementError(
                ResearchRequirementError.DIMENSION_MODEL_MISMATCH
            )

    governed_dimensions, filter_dimensions = _governed_dimensions(
        schema,
        target_metrics,
        dimensions,
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
    scope_dimension_refs = tuple(
        dict.fromkeys(
            (
                *explicit_dimension_refs,
                *governed_dimensions,
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
    driver_metric_refs = tuple(
        dict.fromkeys(
            (
                *_driver_metric_refs(target_metrics, metrics),
                *(item.driver_metric_ref for item in driver_relationships),
            )
        )
    )[:_MAX_SCOPE_DRIVER_METRICS]
    allowed_driver_refs = set(driver_metric_refs)
    driver_relationships = tuple(
        item
        for item in driver_relationships
        if item.driver_metric_ref in allowed_driver_refs
    )
    if not scope_dimension_refs and not driver_metric_refs:
        raise ResearchRequirementError(ResearchRequirementError.SCOPE_EMPTY)

    included_refs = {
        *target_metric_refs,
        *scope_dimension_refs,
        *driver_metric_refs,
    }
    eligible_refs = {
        *(
            _dimension_ref(item)
            for item in schema.dimensions
            if item.model == target_model_id and not _is_time_dimension(item)
        ),
        *(
            _metric_ref(item)
            for item in schema.metrics
            if item.model == target_model_id
        ),
    }
    excluded_asset_refs = tuple(sorted(eligible_refs - included_refs))
    immutable_filters = tuple(
        ResearchFilterBinding(
            target_ref=item.target_ref,
            operator=item.operator,
            value=item.value,
            stage=item.stage,
        )
        for item in semantic_parse.filters
    )
    allowed_actions = _allowed_actions(
        schema,
        target_metrics,
        scope_dimension_refs,
        driver_metric_refs,
        time_roles,
        hierarchies=hierarchies,
        driver_relationships=driver_relationships,
    )
    contribution_dimension_refs = tuple(
        item
        for item in scope_dimension_refs
        if item in set(governed_dimensions) | set(explicit_dimension_refs)
    )
    contribution_metric_refs = (
        target_metric_refs
        if set(time_roles) == {"current", "previous"}
        and target_metrics
        and all(_metric_additivity(schema, item.id) == "FULL" for item in target_metrics)
        else ()
    )
    scope = ResearchScope(
        dimension_refs=scope_dimension_refs,
        target_metric_refs=target_metric_refs,
        driver_metric_refs=driver_metric_refs,
        hierarchies=hierarchies,
        driver_relationships=driver_relationships,
        allowed_filter_refs=allowed_filter_refs,
        contribution_metric_refs=contribution_metric_refs,
        contribution_dimension_refs=contribution_dimension_refs,
        excluded_asset_refs=excluded_asset_refs,
    )
    if not schema.schema_fingerprint:
        raise ResearchRequirementError(
            ResearchRequirementError.SCHEMA_FINGERPRINT_REQUIRED
        )
    scope_fingerprint = _fingerprint(
        {
            "goal": dynamic.goal,
            "reason": dynamic.reason,
            "target_metric_refs": target_metric_refs,
            "time_roles": time_roles,
            "time_bindings": [item.model_dump(mode="json") for item in time_bindings],
            "immutable_filters": [
                item.model_dump(mode="json") for item in immutable_filters
            ],
            "scope": scope.model_dump(mode="json"),
            "allowed_actions": allowed_actions,
            "schema_version": schema.schema_version,
            "contract_version": schema.contract_version,
            "schema_fingerprint": schema.schema_fingerprint,
        }
    )
    return ResearchRequirement(
        goal=dynamic.goal,
        reason=ResearchReason(dynamic.reason),
        target_metric_refs=target_metric_refs,
        time_roles=time_roles,
        time_bindings=time_bindings,
        immutable_filters=immutable_filters,
        scope=scope,
        allowed_actions=allowed_actions,
        budget=budget or ResearchBudget(),
        version_snapshot=ResearchVersionSnapshot(
            schema_version=schema.schema_version,
            contract_version=schema.contract_version,
            schema_fingerprint=schema.schema_fingerprint,
            scope_fingerprint=scope_fingerprint,
        ),
    )


def _governed_dimensions(
    schema: DatasetSchema,
    target_metrics: Sequence[_ResearchSchemaElement],
    dimensions: Mapping[str, _ResearchSchemaElement],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """只投影指标能力契约明确允许的物理维度。"""

    metric_ids = {item.id for item in target_metrics}
    dimension_by_id = {item.id: item for item in dimensions.values()}
    group_refs: list[str] = []
    filter_refs: list[str] = []
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
        usages = {
            str(item).upper() for item in capability.get("usages") or [] if item
        }
        if "GROUP_BY" in usages:
            group_refs.append(ref)
        if "FILTER" in usages:
            filter_refs.append(ref)
    return tuple(dict.fromkeys(group_refs)), tuple(dict.fromkeys(filter_refs))


def _driver_metric_refs(
    target_metrics: Sequence[_ResearchSchemaElement],
    metrics: Mapping[str, _ResearchSchemaElement],
) -> tuple[str, ...]:
    """派生指标的显式 metric_refs 是第一阶段唯一驱动指标来源。"""

    by_id = {item.id: ref for ref, item in metrics.items()}
    target_refs = {_metric_ref(item) for item in target_metrics}
    refs: list[str] = []
    for metric in target_metrics:
        raw_refs = metric.ext_info.get("metric_refs")
        for metric_id in raw_refs if isinstance(raw_refs, list) else []:
            if (
                isinstance(metric_id, int)
                and metric_id in by_id
                and by_id[metric_id] not in target_refs
            ):
                refs.append(by_id[metric_id])
    return tuple(dict.fromkeys(refs))


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
        if not isinstance(raw, dict):
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        if str(raw.get("status") or "").upper() != "CERTIFIED":
            continue
        hierarchy_id = raw.get("id")
        refs = raw.get("dimension_refs")
        if (
            not isinstance(hierarchy_id, str)
            or not hierarchy_id
            or not isinstance(refs, list)
            or len(refs) < 2
            or any(not isinstance(ref, str) for ref in refs)
        ):
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        normalized_refs = tuple(dict.fromkeys(refs))
        if len(normalized_refs) != len(refs):
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
            ResearchHierarchy(id=hierarchy_id, dimension_refs=normalized_refs)
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
    for metric in target_metrics:
        raw_refs = metric.ext_info.get("metric_refs")
        for metric_id in raw_refs if isinstance(raw_refs, list) else []:
            driver_ref = next(
                (ref for ref, item in metric_by_ref.items() if item.id == metric_id),
                None,
            )
            if driver_ref is None or driver_ref in target_refs:
                continue
            if metric_by_ref[driver_ref].model != target_model_id:
                raise ResearchRequirementError(
                    ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
                )
            relationships.append(
                ResearchDriverRelationship(
                    target_metric_ref=_metric_ref(metric),
                    driver_metric_ref=driver_ref,
                    relationship_type="formula_component",
                    dimension_refs=_formula_relationship_dimensions(
                        schema,
                        target_metric_id=metric.id,
                        driver_metric_id=metric_by_ref[driver_ref].id,
                        scope_dimension_refs=dimension_refs,
                    ),
                    time_roles=time_roles,
                    relationship_fingerprint=_fingerprint(
                        {
                            "type": "formula_component",
                            "target": _metric_ref(metric),
                            "driver": driver_ref,
                            "dimensions": _formula_relationship_dimensions(
                                schema,
                                target_metric_id=metric.id,
                                driver_metric_id=metric_by_ref[driver_ref].id,
                                scope_dimension_refs=dimension_refs,
                            ),
                            "time_roles": time_roles,
                        }
                    ),
                )
            )

    for raw in schema.research_relationships:
        if not isinstance(raw, dict):
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        target_ref = raw.get("target_metric_ref")
        driver_ref = raw.get("driver_metric_ref")
        if target_ref not in target_refs:
            continue
        if not isinstance(driver_ref, str) or driver_ref not in metric_by_ref:
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        if metric_by_ref[driver_ref].model != target_model_id:
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        status = raw.get("status")
        if not isinstance(status, str):
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        if status.upper() != "CERTIFIED":
            continue
        relationship_type = raw.get("relationship_type")
        if relationship_type not in {
            "formula_component",
            "certified_driver",
            "governed_analysis_relation",
        }:
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        raw_dimensions = raw.get("dimension_refs", raw.get("supported_dimension_refs"))
        raw_time_roles = raw.get("time_roles", raw.get("supported_time_roles"))
        fingerprint = raw.get("relationship_fingerprint")
        if (
            not isinstance(raw_dimensions, list)
            or not isinstance(raw_time_roles, list)
            or not isinstance(fingerprint, str)
            or not fingerprint
        ):
            raise ResearchRequirementError(
                ResearchRequirementError.GOVERNANCE_CONTRACT_INVALID
            )
        normalized_dimensions = tuple(dict.fromkeys(raw_dimensions))
        normalized_time_roles = tuple(dict.fromkeys(raw_time_roles))
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
                relationship_type=relationship_type,
                dimension_refs=normalized_dimensions,
                time_roles=normalized_time_roles,
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
    common = supported_by_metric[target_metric_id] & supported_by_metric[driver_metric_id]
    return tuple(ref for ref in scope_dimension_refs if ref in common)


def _time_bindings(
    semantic_parse: SemanticParseOutput,
    schema: DatasetSchema,
    model_id: int,
    temporal_context: TemporalContext | None,
) -> tuple[tuple[str, ...], tuple[ResearchTimeBinding, ...]]:
    if not semantic_parse.time_filters:
        return ("single",), ()
    roles = tuple(item.role for item in semantic_parse.time_filters)
    if len(roles) != len(set(roles)):
        raise ResearchRequirementError(
            ResearchRequirementError.TIME_ROLE_DUPLICATED
        )
    if temporal_context is None:
        raise ResearchRequirementError(
            ResearchRequirementError.TIME_CONTEXT_REQUIRED
        )
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
    for item in semantic_parse.time_filters:
        normalized = resolve_time_range(item.expression, temporal_context)
        if not isinstance(normalized, dict) or normalized.get("kind") == "unsupported":
            raise ResearchRequirementError(
                ResearchRequirementError.TIME_RANGE_UNRESOLVED
            )
        bindings.append(
            ResearchTimeBinding(
                role=item.role,
                expression=item.expression,
                dimension_ref=_dimension_ref(time_dimension),
                dimension_id=time_dimension.id,
                normalized=normalized,
            )
        )
    return roles, tuple(bindings)


def _allowed_actions(
    schema: DatasetSchema,
    target_metrics: Sequence[_ResearchSchemaElement],
    dimension_refs: tuple[str, ...],
    driver_metric_refs: tuple[str, ...],
    time_roles: tuple[str, ...],
    *,
    hierarchies: tuple[ResearchHierarchy, ...],
    driver_relationships: tuple[ResearchDriverRelationship, ...],
) -> tuple[ResearchActionType, ...]:
    actions = [ResearchActionType.COMPARE]
    if dimension_refs:
        actions.extend(
            (
                ResearchActionType.BREAKDOWN,
                ResearchActionType.FILTER_FROM_RESULT,
            )
        )
    if (
        dimension_refs
        and set(time_roles) == {"current", "previous"}
        and all(
            _metric_additivity(schema, item.id) == "FULL"
            for item in target_metrics
        )
    ):
        actions.append(ResearchActionType.CONTRIBUTION)
    if hierarchies:
        actions.append(ResearchActionType.DRILLDOWN)
    if driver_metric_refs and driver_relationships:
        actions.append(ResearchActionType.VALIDATE_HYPOTHESIS)
    actions.append(ResearchActionType.FINISH)
    return tuple(actions)


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


def _is_time_dimension(dimension: _ResearchSchemaElement) -> bool:
    return bool(dimension.ext_info.get("is_default_time"))


def _metric_ref(metric: _ResearchSchemaElement) -> str:
    return f"METRIC:{metric.id}:{metric.model}"


def _dimension_ref(dimension: _ResearchSchemaElement) -> str:
    return f"DIMENSION:{dimension.id}:{dimension.model}"


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["build_research_requirement"]
