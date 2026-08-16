"""基于运行时语义 Schema 生成不可变查询计划。"""

from __future__ import annotations

import hashlib
import json

from apps.semantic.errors import SemanticValidationError
from apps.semantic.models.dto import (
    DatasetSchema,
    SemanticAggregationPlan,
    SemanticDimensionBinding,
    SemanticFilterBinding,
    SemanticMetricBinding,
    SemanticModelPlan,
    SemanticPlanStatus,
    SemanticQueryPlan,
    SemanticQueryPlanningInput,
    SemanticTimeBinding,
)
from apps.semantic.services.query.validation import SemanticQueryValidationService


class SemanticQueryPlanningService:
    """只根据发布后的 Schema 生成候选计划，不执行 SQL。"""

    def __init__(self, validator: SemanticQueryValidationService | None = None):
        self._validator = validator or SemanticQueryValidationService()

    def plan(
        self,
        schema: DatasetSchema,
        request: SemanticQueryPlanningInput,
    ) -> SemanticQueryPlan:
        if request.dataset_id != schema.data_set.id:
            raise SemanticValidationError("SEMANTIC_QUERY_DATASET_MISMATCH")
        if not request.metric_ids:
            raise SemanticValidationError("SEMANTIC_QUERY_METRIC_REQUIRED")

        metric_elements = {
            item.id: item for item in schema.metrics if item.id in request.metric_ids
        }
        missing_metric_ids = [item for item in request.metric_ids if item not in metric_elements]
        if missing_metric_ids:
            raise SemanticValidationError("SEMANTIC_QUERY_METRIC_NOT_FOUND")

        metric_contracts = _by_id(schema.metric_contracts, "metric_id")
        metric_bindings = tuple(
            SemanticMetricBinding(
                metric_id=metric_id,
                model_id=int(metric_elements[metric_id].model or 0),
                version=int(
                    (metric_contracts.get(metric_id) or {}).get("contract_version") or 0
                ),
                aggregation=metric_elements[metric_id].default_agg,
                result_grain=tuple(
                    (metric_contracts.get(metric_id) or {}).get("result_grain") or ()
                ),
                additivity=(metric_contracts.get(metric_id) or {}).get("additivity"),
                time_semantics=(metric_contracts.get(metric_id) or {}).get("time_semantics"),
                metric_refs=_metric_refs(metric_elements[metric_id]),
            )
            for metric_id in request.metric_ids
        )
        base_model_id = metric_bindings[0].model_id
        if base_model_id <= 0:
            raise SemanticValidationError("SEMANTIC_QUERY_METRIC_MODEL_REQUIRED")

        dimension_bindings, pre_aggregation, pre_aggregation_grain = self._bind_dimensions(
            schema,
            request,
            metric_bindings,
        )
        filters = self._bind_filters(request, dimension_bindings)
        time_binding = self._bind_time(schema, request, metric_bindings)
        relation_path = tuple(
            relation_id
            for binding in dimension_bindings
            for relation_id in binding.relation_path
        )
        relation_path = tuple(dict.fromkeys(relation_path))
        model_ids = tuple(
            dict.fromkeys(
                [
                    base_model_id,
                    *(item.model_id for item in dimension_bindings),
                    *self._relation_model_ids(schema, relation_path),
                ]
            )
        )
        result_grain = _common_result_grain(metric_bindings)
        plan = SemanticQueryPlan(
            plan_id="pending",
            dataset_id=request.dataset_id,
            schema_version=request.schema_version,
            contract_version=request.contract_version,
            metrics=metric_bindings,
            dimensions=dimension_bindings,
            filters=filters,
            time_binding=time_binding,
            model_plan=SemanticModelPlan(
                base_model_id=base_model_id,
                model_ids=model_ids,
                relation_path=relation_path,
                pre_aggregation_required=pre_aggregation,
                pre_aggregation_grain=pre_aggregation_grain,
            ),
            aggregation_plan=SemanticAggregationPlan(
                aggregations={item.metric_id: item.aggregation for item in metric_bindings},
                distinct_keys={
                    item.metric_id: tuple(
                        (metric_contracts.get(item.metric_id) or {}).get("distinct_keys") or ()
                    )
                    for item in metric_bindings
                },
                result_grain=result_grain,
                snapshot_strategies={
                    item.metric_id: (metric_contracts.get(item.metric_id) or {}).get(
                        "snapshot_aggregation"
                    )
                    for item in metric_bindings
                },
            ),
            validation_status=SemanticPlanStatus.PROVEN,
            query_shape=dict(request.query_shape),
            having=request.having,
            time_offset=request.time_offset,
            subplans=request.subplans,
            order_by=tuple(request.order_by),
            limit=request.limit,
            fingerprint="pending",
        )
        fingerprint = _plan_fingerprint(plan)
        plan = plan.model_copy(
            update={
                "plan_id": f"semantic-plan-{fingerprint[:16]}",
                "fingerprint": fingerprint,
            }
        )
        report = self._validator.validate(plan, schema)
        return plan.model_copy(
            update={
                "validation_status": report.status,
                "validation_reason_codes": report.reason_codes,
            }
        )

    def _bind_dimensions(
        self,
        schema: DatasetSchema,
        request: SemanticQueryPlanningInput,
        metrics: tuple[SemanticMetricBinding, ...],
    ) -> tuple[tuple[SemanticDimensionBinding, ...], bool, tuple[str, ...]]:
        logical_dimensions = _by_id(schema.logical_dimensions, "id")
        physical_dimensions = list(schema.dimensions)
        capabilities = [
            item
            for item in schema.metric_dimension_capabilities
            if isinstance(item, dict)
        ]
        bindings: list[SemanticDimensionBinding] = []
        pre_aggregation = False
        pre_aggregation_grain: tuple[str, ...] = ()
        for logical_id in request.logical_dimension_ids:
            if logical_id not in logical_dimensions:
                raise SemanticValidationError("SEMANTIC_QUERY_LOGICAL_DIMENSION_NOT_FOUND")
            metric_id = metrics[0].metric_id
            candidates = [
                item
                for item in capabilities
                if item.get("metric_id") == metric_id
                and item.get("logical_dimension_id") == logical_id
            ]
            if not candidates:
                fallback_dimension = next(
                    (
                        item
                        for item in physical_dimensions
                        if item.model == metrics[0].model_id
                        and int(item.ext_info.get("logical_dimension_id") or -1) == logical_id
                    ),
                    None,
                )
                bindings.append(
                    SemanticDimensionBinding(
                        logical_dimension_id=logical_id,
                        physical_dimension_id=int(fallback_dimension.id if fallback_dimension else 1),
                        model_id=metrics[0].model_id,
                        usages=request.dimension_usages.get(logical_id, ()),
                        version=0,
                        aggregation_safety="FORBIDDEN",
                    )
                )
                continue
            candidates.sort(key=lambda item: int(item.get("id") or 0))
            capability = candidates[0]
            target_model_id = int(capability.get("target_model_id") or metrics[0].model_id)
            physical_candidates = [
                item
                for item in physical_dimensions
                if item.model == target_model_id
                and int(item.ext_info.get("logical_dimension_id") or -1) == logical_id
            ]
            physical_id = capability.get("physical_dimension_id")
            if physical_id is None and physical_candidates:
                physical_id = physical_candidates[0].id
            bindings.append(
                SemanticDimensionBinding(
                    logical_dimension_id=logical_id,
                    physical_dimension_id=int(physical_id or 1),
                    model_id=target_model_id,
                    usages=request.dimension_usages.get(logical_id, ()),
                    version=int(capability.get("version") or 0),
                    relation_path=tuple(capability.get("relation_path") or ()),
                    aggregation_safety=str(capability.get("aggregation_safety") or "FORBIDDEN"),
                )
            )
            if capability.get("aggregation_safety") == "PRE_AGGREGATE_REQUIRED":
                pre_aggregation = True
                pre_aggregation_grain = tuple(capability.get("pre_aggregation_grain") or ())
        return tuple(bindings), pre_aggregation, pre_aggregation_grain

    @staticmethod
    def _bind_filters(
        request: SemanticQueryPlanningInput,
        dimensions: tuple[SemanticDimensionBinding, ...],
    ) -> tuple[SemanticFilterBinding, ...]:
        physical_by_logical = {
            item.logical_dimension_id: item.physical_dimension_id for item in dimensions
        }
        result: list[SemanticFilterBinding] = []
        for item in request.filters:
            logical_id = item.get("logical_dimension_id")
            # 检索槽位使用 asset_id 表示已绑定的物理维度；内部计划 DTO
            # 使用 physical_dimension_id。两种入口都必须落到同一物理资产。
            physical_id = item.get("physical_dimension_id") or item.get("asset_id")
            if physical_id is None and isinstance(logical_id, int):
                physical_id = physical_by_logical.get(logical_id)
            if not isinstance(physical_id, int) or physical_id <= 0:
                physical_id = 1
            result.append(
                SemanticFilterBinding(
                    physical_dimension_id=physical_id,
                    operator=str(item.get("operator") or "="),
                    value=item.get("value"),
                    value_source=str(item.get("value_source") or "USER"),
                )
            )
        return tuple(result)

    @staticmethod
    def _bind_time(
        schema: DatasetSchema,
        request: SemanticQueryPlanningInput,
        metrics: tuple[SemanticMetricBinding, ...],
    ) -> SemanticTimeBinding:
        contracts = _by_id(schema.metric_contracts, "metric_id")
        semantics = {item.time_semantics for item in metrics if item.time_semantics}
        semantic = next(iter(semantics), "NONE") if len(semantics) <= 1 else "MIXED"
        default_dimension_id = next(
            (
                (contracts.get(item.metric_id) or {}).get("default_time_dimension_id")
                for item in metrics
                if (contracts.get(item.metric_id) or {}).get("default_time_dimension_id")
                is not None
            ),
            None,
        )
        return SemanticTimeBinding(
            semantics=semantic,
            dimension_id=request.time_dimension_id or default_dimension_id,
            time_range=request.time_range,
            grain=request.time_grain,
            snapshot_aggregation=next(
                (
                    (contracts.get(item.metric_id) or {}).get("snapshot_aggregation")
                    for item in metrics
                    if (contracts.get(item.metric_id) or {}).get("snapshot_aggregation")
                ),
                None,
            ),
        )

    @staticmethod
    def _relation_model_ids(schema: DatasetSchema, relation_path: tuple[int, ...]) -> tuple[int, ...]:
        relations = _by_id(schema.relation_contracts, "relation_id")
        result: list[int] = []
        for relation_id in relation_path:
            relation = relations.get(relation_id) or {}
            for key in ("left_model_id", "right_model_id"):
                model_id = relation.get(key)
                if isinstance(model_id, int) and model_id not in result:
                    result.append(model_id)
        return tuple(result)


def _by_id(items: list[dict], key: str) -> dict[int, dict]:
    return {
        int(item[key]): item
        for item in items
        if isinstance(item, dict) and item.get(key) is not None
    }


def _common_result_grain(metrics: tuple[SemanticMetricBinding, ...]) -> tuple[str, ...]:
    if not metrics:
        return ()
    return metrics[0].result_grain


def _metric_refs(metric) -> tuple[int, ...]:
    """从运行时指标契约提取派生指标引用。"""

    params = metric.type_params or {}
    metric_params = params.get("metricDefineByMetricParams") or {}
    references = metric_params.get("metrics") if isinstance(metric_params, dict) else []
    return tuple(
        item.get("id")
        for item in references or []
        if isinstance(item, dict)
        and isinstance(item.get("id"), int)
        and item["id"] > 0
    )


def _plan_fingerprint(plan: SemanticQueryPlan) -> str:
    payload = plan.model_dump(
        mode="json",
        exclude={"plan_id", "fingerprint", "validation_status", "validation_reason_codes"},
    )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
