"""根据执行需求生成并冻结严格语义查询计划。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from apps.chatbi.models.dto.execution_requirement import QueryRequirement
from apps.semantic import (
    SemanticQueryPlanningInput,
    SemanticQueryPlanningService,
    SemanticQueryValidationService,
)
from apps.tool.tools.semantic_contracts import SemanticAssetScope


def prepare_strict_query_scope(
    scope: SemanticAssetScope,
    requirements: Sequence[QueryRequirement],
    *,
    schema_provider: Any,
    workspace_id: int,
    dataset_id: int,
) -> SemanticAssetScope:
    """为每个执行需求生成独立 PROVEN 计划，并写回统一语义范围。"""

    if scope.semantic_enforcement != "STRICT":
        return scope
    if schema_provider is None:
        raise ValueError("SEMANTIC_SCHEMA_PROVIDER_REQUIRED")
    if not isinstance(dataset_id, int) or isinstance(dataset_id, bool) or dataset_id <= 0:
        raise ValueError("EXECUTION_REQUIREMENT_DATASET_REQUIRED")
    if not requirements:
        raise ValueError("EXECUTION_REQUIREMENT_QUERY_REQUIRED")

    schema = schema_provider.build_dataset_schema(workspace_id, dataset_id)
    physical_dimensions = {item.id: item for item in schema.dimensions}
    plans = []
    reports = []
    allowed_assets: list[dict[str, Any]] = []
    for requirement in requirements:
        plan, report, assets = _prepare_requirement_plan(
            requirement,
            schema=schema,
            physical_dimensions=physical_dimensions,
            dataset_id=dataset_id,
        )
        plans.append(plan)
        reports.append(report)
        allowed_assets.extend(assets)

    # 计划按执行需求顺序保存，QueryTask 通过来源需求 ID 精确选择计划。
    scope_payload = scope.model_dump(mode="json")
    scope_payload.update(
        {
            "decision_status": "resolved",
            "allowed_assets": _deduplicate_assets(allowed_assets),
            "query_plan": plans[0].model_dump(mode="json"),
            "query_plans": [plan.model_dump(mode="json") for plan in plans],
            "validation_report": reports[0].model_dump(mode="json"),
            "validation_reports": [report.model_dump(mode="json") for report in reports],
        }
    )
    return SemanticAssetScope.model_validate(scope_payload)


def _prepare_requirement_plan(
    requirement: QueryRequirement,
    *,
    schema: Any,
    physical_dimensions: dict[int, Any],
    dataset_id: int,
) -> tuple[Any, Any, list[dict[str, Any]]]:
    """把一个 QueryRequirement 转换为语义规划服务的受控输入。"""

    metric_ids = tuple(_asset_id(item, "metric") for item in requirement.metrics)
    having_items = requirement.having or tuple(
        item
        for item in requirement.filters
        if str(item.get("stage") or "where").lower() == "having"
    )
    where_items = tuple(
        item
        for item in requirement.filters
        if str(item.get("stage") or "where").lower() != "having"
    )
    dimension_items = [
        *requirement.group_by,
        *where_items,
        *having_items,
    ]
    logical_dimension_ids: list[int] = []
    dimension_usages: dict[int, tuple[str, ...]] = {}
    for item in dimension_items:
        asset_id = _asset_id(item, "dimension")
        physical = physical_dimensions.get(asset_id)
        logical_id = physical.ext_info.get("logical_dimension_id") if physical else None
        if not isinstance(logical_id, int) or logical_id <= 0:
            raise ValueError(f"SEMANTIC_LOGICAL_DIMENSION_REQUIRED:{asset_id}")
        if logical_id not in logical_dimension_ids:
            logical_dimension_ids.append(logical_id)
        usage = "GROUP_BY" if item in requirement.group_by else "FILTER"
        previous = dimension_usages.get(logical_id, ())
        if usage not in previous:
            dimension_usages[logical_id] = (*previous, usage)

    time = requirement.time or {}
    normalized_time = time.get("normalized")
    raw_query_shape = dict(requirement.query_shape or {})
    query_shape = {
        "select_mode": raw_query_shape.get("select_mode") or "aggregate",
        "needs_group_by": bool(requirement.group_by),
        **raw_query_shape,
        # 来源需求 ID 是严格计划与 QueryTask 的受控关联键，禁止由输入覆盖。
        "source_requirement_id": requirement.id,
    }
    plan_input = SemanticQueryPlanningInput(
        dataset_id=dataset_id,
        schema_version=schema.schema_version,
        contract_version=schema.contract_version,
        metric_ids=metric_ids,
        logical_dimension_ids=tuple(logical_dimension_ids),
        dimension_usages=dimension_usages,
        filters=tuple(
            {
                "physical_dimension_id": _asset_id(item, "dimension"),
                "operator": item.get("operator") or "=",
                "value": item.get("value"),
                "value_source": "USER",
            }
            for item in where_items
        ),
        time_range=normalized_time if isinstance(normalized_time, dict) else None,
        time_dimension_id=_optional_asset_id(time.get("dimension_id")),
        time_grain=(str(time["grain"]) if time.get("grain") else None),
        select_mode=str(query_shape.get("select_mode") or "aggregate"),
        query_shape=query_shape,
        having=tuple(
            {
                "physical_dimension_id": _asset_id(item, "dimension"),
                "operator": item.get("operator") or "=",
                "value": item.get("value"),
                "value_source": "USER",
            }
            for item in having_items
        ),
        time_offset=requirement.time_offset,
        order_by=requirement.order_by,
        limit=requirement.limit,
    )
    plan = SemanticQueryPlanningService().plan(schema, plan_input)
    report = SemanticQueryValidationService().validate(plan, schema)
    if plan.validation_status.value != "PROVEN" or report.status.value != "PROVEN":
        reasons = list(report.reason_codes or plan.validation_reason_codes)
        raise ValueError(
            "SEMANTIC_QUERY_PLAN_NOT_PROVEN"
            + (":" + ",".join(reasons) if reasons else "")
        )

    assets = [
        {
            "asset_type": "METRIC",
            "asset_id": item["asset_id"],
            "model_id": item.get("model_id"),
        }
        for item in requirement.metrics
    ]
    assets.extend(
        {
            "asset_type": "DIMENSION",
            "asset_id": item["asset_id"],
            "model_id": item.get("model_id"),
        }
        for item in [*requirement.group_by, *where_items, *having_items]
        if _optional_asset_id(item.get("asset_id")) is not None
    )
    if _optional_asset_id(time.get("dimension_id")) is not None:
        assets.append(
            {
                "asset_type": "DIMENSION",
                "asset_id": time["dimension_id"],
                "model_id": None,
            }
        )
    return (
        plan.model_copy(update={"output_aliases": dict(requirement.output_aliases)}),
        report,
        assets,
    )


def _deduplicate_assets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, int, Any]] = set()
    for item in items:
        key = (str(item["asset_type"]), int(item["asset_id"]), item.get("model_id"))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _asset_id(item: dict[str, Any], kind: str) -> int:
    value = item.get("asset_id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"EXECUTION_REQUIREMENT_{kind.upper()}_ID_REQUIRED")
    return value


def _optional_asset_id(value: Any) -> int | None:
    return None if isinstance(value, bool) or not isinstance(value, int) or value <= 0 else value


__all__ = ["prepare_strict_query_scope"]
