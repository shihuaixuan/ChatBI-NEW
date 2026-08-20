"""补充资产定义、生成执行需求并选择 Fast 或 Plan。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chatbi.models.dto.execution_requirement import (
    CalculationOperation,
    ExecutionRequirement,
    ExecutionRoute,
)
from apps.chatbi.models.dto.semantic_parse import SemanticParseOutput
from apps.chatbi.models.orm.agent_run import AgentExecutionMode
from apps.semantic.models.dto import DatasetSchema, SchemaElement
from apps.semantic.services.schema_service import DatasetSchemaProvider
from apps.temporal import TemporalContext
from apps.temporal.resolver import resolve_time_range


class ModeRoutingError(ValueError):
    """无法生成可执行需求，或请求的模式不允许。"""


@dataclass(frozen=True, slots=True)
class ModeRouteInput:
    """已经校验过的语义解析结果和候选资产。"""

    semantic_parse: SemanticParseOutput
    candidate_groups: dict[str, list[dict[str, Any]]]
    dataset_id: int
    tenant_id: int
    enabled_modes: tuple[str, ...] = (
        AgentExecutionMode.FAST.value,
        AgentExecutionMode.PLAN.value,
    )
    temporal_context: TemporalContext | None = None
    datasource_id: int | None = None


class ModeRouter:
    """根据语义解析结果和候选资产生成执行需求。"""

    def __init__(self, schema_provider: DatasetSchemaProvider) -> None:
        if schema_provider is None:
            raise ValueError("MODE_ROUTER_SCHEMA_PROVIDER_REQUIRED")
        self._schema_provider = schema_provider

    def route(self, request: ModeRouteInput) -> dict[str, Any]:
        """完成资产补充、绑定校验、执行需求分析和模式判断。"""

        enabled = _normalize_modes(request.enabled_modes)
        semantic_parse = request.semantic_parse
        if semantic_parse.status != "resolved" or semantic_parse.unresolved:
            raise ModeRoutingError("SEMANTIC_PARSE_NOT_RESOLVED")

        schema = self._schema_provider.build_dataset_schema(
            request.tenant_id,
            request.dataset_id,
        )
        if (
            semantic_parse.multi_step is not None
            and semantic_parse.multi_step.type == "dynamic_research"
        ):
            if AgentExecutionMode.RESEARCH.value not in enabled:
                raise ModeRoutingError("EXECUTION_MODE_NOT_AVAILABLE:research")
            return ExecutionRequirement(
                status="ready",
                route=ExecutionRoute(
                    mode=AgentExecutionMode.RESEARCH.value,
                    reasons=(
                        "dynamic_research",
                        semantic_parse.multi_step.reason,
                    ),
                ),
                runtime={
                    "tenant_id": request.tenant_id,
                    "datasource_id": request.datasource_id,
                    "dataset_id": request.dataset_id,
                    "schema_version": schema.schema_version,
                    "contract_version": schema.contract_version,
                    "research_goal": semantic_parse.multi_step.goal,
                },
                asset_snapshot={
                    "schema_version": schema.schema_version,
                    "contract_version": schema.contract_version,
                    "schema_fingerprint": schema.schema_fingerprint,
                },
            ).model_dump(mode="json")
        selected_refs = _selected_refs(semantic_parse)
        candidates = self._resolve_candidates(request, schema, set(selected_refs))
        missing_refs = sorted(set(selected_refs) - set(candidates))
        if missing_refs:
            raise ModeRoutingError(
                "SEMANTIC_PARSE_CANDIDATE_NOT_FOUND:" + ",".join(missing_refs)
            )

        execution = self._build_execution_requirements(request, schema, candidates)
        mode, reasons = self._select_mode(execution)
        if mode.value not in enabled:
            raise ModeRoutingError(f"EXECUTION_MODE_NOT_AVAILABLE:{mode.value}")

        return ExecutionRequirement(
            status="ready",
            route=ExecutionRoute(
                mode=(
                    AgentExecutionMode.FAST.value
                    if mode is AgentExecutionMode.FAST
                    else AgentExecutionMode.PLAN.value
                ),
                reasons=tuple(dict.fromkeys(reasons)),
            ),
            **execution,
            runtime={
                "tenant_id": request.tenant_id,
                "datasource_id": request.datasource_id,
                "dataset_id": request.dataset_id,
                "schema_version": schema.schema_version,
                "contract_version": schema.contract_version,
            },
            asset_snapshot={
                "schema_version": schema.schema_version,
                "contract_version": schema.contract_version,
                "schema_fingerprint": schema.schema_fingerprint,
            },
            unresolved=(),
        ).model_dump(mode="json")

    def _resolve_candidates(
        self,
        request: ModeRouteInput,
        schema: DatasetSchema,
        selected_refs: set[str],
    ) -> dict[str, dict[str, Any]]:
        """从候选 ref 补充语义层定义，候选不存在定义时直接失败。"""

        elements = {
            **{f"METRIC:{item.id}:{item.model}": item for item in schema.metrics},
            **{f"DIMENSION:{item.id}:{item.model}": item for item in schema.dimensions},
        }
        resolved: dict[str, dict[str, Any]] = {}
        for items in request.candidate_groups.values():
            for candidate in items:
                if not isinstance(candidate, dict) or not candidate.get("ref"):
                    continue
                ref = str(candidate["ref"])
                if ref not in selected_refs:
                    continue
                element = elements.get(ref)
                if element is None:
                    raise ModeRoutingError(f"SEMANTIC_ASSET_DEFINITION_NOT_FOUND:{ref}")
                resolved[ref] = _asset_definition(element)
        return resolved

    def _build_execution_requirements(
        self,
        request: ModeRouteInput,
        schema: DatasetSchema,
        candidates: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """按模型和时间角色拆分查询，并生成查询后计算要求。"""

        semantic_parse = request.semantic_parse
        if semantic_parse.multi_step is not None:
            return self._build_multi_step_requirements(
                request,
                schema,
                candidates,
            )
        metric_refs = [item.ref for item in semantic_parse.measures]
        dimension_refs = [item.ref for item in semantic_parse.group_by]
        selected_refs = _selected_refs(semantic_parse)
        for ref in selected_refs:
            if ref not in candidates:
                raise ModeRoutingError(f"SEMANTIC_ASSET_BINDING_REQUIRED:{ref}")

        model_ids = {
            candidates[ref]["model_id"]
            for ref in selected_refs
            if candidates[ref].get("model_id") is not None
        }
        if not model_ids:
            raise ModeRoutingError("SEMANTIC_EXECUTABLE_MODEL_REQUIRED")

        time_filters: list[Any] = list(semantic_parse.time_filters) or [None]
        query_requirements: list[dict[str, Any]] = []
        for model_id in sorted(model_ids):
            model_metrics = [
                candidates[ref]
                for ref in metric_refs
                if candidates[ref]["model_id"] == model_id
            ]
            if not model_metrics:
                continue
            model_dimensions = [
                candidates[ref]
                for ref in dimension_refs
                if candidates[ref]["model_id"] == model_id
            ]
            model_filters = [
                _filter_requirement(item, candidates[item.target_ref])
                for item in semantic_parse.filters
                if candidates[item.target_ref]["model_id"] == model_id
            ]
            model_orders = [
                _order_requirement(item, candidates[item.target_ref])
                for item in semantic_parse.order_by
                if candidates[item.target_ref]["model_id"] == model_id
            ]
            for index, time_filter in enumerate(time_filters):
                time_requirement = _time_requirement(
                    schema,
                    model_id,
                    time_filter,
                    request.temporal_context,
                )
                role = time_filter.role if time_filter is not None else "single"
                query_requirements.append(
                    {
                        "id": _query_requirement_id(model_metrics, role, index),
                        "model_ref": f"MODEL:{model_id}",
                        "metrics": model_metrics,
                        "group_by": model_dimensions,
                        "filters": model_filters,
                        "time": time_requirement,
                        "order_by": model_orders,
                        "limit": semantic_parse.limit,
                    }
                )

        if not query_requirements:
            raise ModeRoutingError("SEMANTIC_QUERY_REQUIREMENT_EMPTY")

        covered_refs = {
            item["ref"]
            for query in query_requirements
            for item in (*query["metrics"], *query["group_by"])
        }
        covered_refs.update(
            item["target_ref"]
            for query in query_requirements
            for item in query["filters"]
        )
        covered_refs.update(
            item["target_ref"]
            for query in query_requirements
            for item in query["order_by"]
        )
        missing_execution_refs = sorted(set(selected_refs) - covered_refs)
        if missing_execution_refs:
            raise ModeRoutingError(
                "SEMANTIC_EXECUTION_ASSET_NOT_COVERED:"
                + ",".join(missing_execution_refs)
            )

        post_calculations = [
            _calculation_requirement(item, query_requirements)
            for item in semantic_parse.calculations
        ]
        if len(query_requirements) > 1 and not post_calculations:
            post_calculations.append(_merge_requirement(query_requirements))
        return {
            "query_requirements": query_requirements,
            "post_calculations": post_calculations,
        }

    def _build_multi_step_requirements(
        self,
        request: ModeRouteInput,
        schema: DatasetSchema,
        candidates: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """把固定多步语义确定性展开为完整查询和计算需求。"""

        multi_step = request.semantic_parse.multi_step
        if multi_step is None:
            raise ModeRoutingError("SEMANTIC_MULTI_STEP_REQUIRED")
        if multi_step.type == "fixed_drilldown":
            return _build_fixed_drilldown_requirements(
                request,
                schema,
                candidates,
            )
        if multi_step.type == "fixed_attribution":
            return _build_fixed_attribution_requirements(
                request,
                schema,
                candidates,
            )
        raise ModeRoutingError("SEMANTIC_MULTI_STEP_TYPE_UNSUPPORTED")

    @staticmethod
    def _select_mode(execution: dict[str, Any]) -> tuple[AgentExecutionMode, list[str]]:
        """只依据已生成的执行需求选择模式。"""

        queries = execution["query_requirements"]
        models = {item["model_ref"] for item in queries}
        calculations = execution["post_calculations"]
        reasons: list[str] = []
        if len(models) > 1:
            reasons.append("multiple_models")
        if len(queries) > 1:
            reasons.append("multiple_queries")
        if calculations:
            reasons.append("post_query_calculation")
        result_contract = execution.get("result_contract")
        if isinstance(result_contract, dict):
            analysis_type = result_contract.get("analysis_type")
            if analysis_type == "fixed_attribution" or (
                analysis_type == "fixed_drilldown"
                and (len(queries) > 1 or calculations)
            ):
                reasons.append(str(analysis_type))
        if not reasons:
            return AgentExecutionMode.FAST, ["single_model", "single_query"]
        return AgentExecutionMode.PLAN, reasons


def _selected_refs(semantic_parse: SemanticParseOutput) -> list[str]:
    refs = [
        *(item.ref for item in semantic_parse.measures),
        *(item.ref for item in semantic_parse.group_by),
        *(item.target_ref for item in semantic_parse.filters),
        *(item.target_ref for item in semantic_parse.order_by),
    ]
    multi_step = semantic_parse.multi_step
    if multi_step is not None and multi_step.type == "fixed_drilldown":
        refs.extend(multi_step.metric_refs)
        refs.extend(
            ref for level in multi_step.levels for ref in level.dimension_refs
        )
    elif multi_step is not None and multi_step.type == "fixed_attribution":
        refs.extend((multi_step.metric_ref, multi_step.dimension_ref))
    return list(dict.fromkeys(refs))


def _build_fixed_drilldown_requirements(
    request: ModeRouteInput,
    schema: DatasetSchema,
    candidates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """展开固定下钻；层级结果保持独立，不使用 merge 混合粒度。"""

    spec = request.semantic_parse.multi_step
    if spec is None or spec.type != "fixed_drilldown":
        raise ModeRoutingError("SEMANTIC_FIXED_DRILLDOWN_REQUIRED")
    if spec.primary_level not in {level.id for level in spec.levels}:
        raise ModeRoutingError("SEMANTIC_DRILLDOWN_PRIMARY_LEVEL_UNKNOWN")
    metric_defs = [candidates[ref] for ref in spec.metric_refs]
    model_ids = {item["model_id"] for item in metric_defs}
    if len(model_ids) != 1:
        raise ModeRoutingError("SEMANTIC_DRILLDOWN_SINGLE_MODEL_REQUIRED")
    model_id = next(iter(model_ids))
    previous_dimensions: tuple[str, ...] = ()
    for level in spec.levels:
        if level.dimension_refs[: len(previous_dimensions)] != previous_dimensions:
            raise ModeRoutingError("SEMANTIC_DRILLDOWN_LEVEL_ORDER_INVALID")
        if any(candidates[ref]["model_id"] != model_id for ref in level.dimension_refs):
            raise ModeRoutingError("SEMANTIC_DRILLDOWN_DIMENSION_MODEL_MISMATCH")
        previous_dimensions = level.dimension_refs

    time_filters = list(request.semantic_parse.time_filters) or [None]
    if len(time_filters) not in {1, 2}:
        raise ModeRoutingError("SEMANTIC_DRILLDOWN_TIME_COUNT_UNSUPPORTED")
    if len(time_filters) == 2 and {item.role for item in time_filters} != {
        "current",
        "previous",
    }:
        raise ModeRoutingError("SEMANTIC_DRILLDOWN_TIME_ROLES_INVALID")
    common_filters = [
        _filter_requirement(item, candidates[item.target_ref])
        for item in request.semantic_parse.filters
        if candidates[item.target_ref]["model_id"] == model_id
    ]
    query_requirements: list[dict[str, Any]] = []
    level_ids = ["total"] if spec.include_total else []
    level_ids.extend(level.id for level in spec.levels)
    dimensions_by_level = {
        "total": [],
        **{
            level.id: [candidates[ref] for ref in level.dimension_refs]
            for level in spec.levels
        },
    }
    for level_id in level_ids:
        for time_filter in time_filters:
            role = time_filter.role if time_filter is not None else "single"
            query_requirements.append(
                {
                    "id": f"drilldown_{level_id}_{role}",
                    "model_ref": f"MODEL:{model_id}",
                    "metrics": metric_defs,
                    "group_by": dimensions_by_level[level_id],
                    "filters": common_filters,
                    "time": _time_requirement(
                        schema,
                        model_id,
                        time_filter,
                        request.temporal_context,
                    ),
                    "order_by": [],
                    "limit": request.semantic_parse.limit,
                    "query_shape": {
                        "shape": "fixed_drilldown",
                        "level_id": level_id,
                    },
                }
            )

    result_ids: list[str] = []
    calculations: list[dict[str, Any]] = []
    if len(time_filters) == 2:
        metric_names = [item["biz_name"] for item in metric_defs]
        for level_id in level_ids:
            result_id = f"drilldown_{level_id}_difference"
            result_ids.append(result_id)
            level_queries = [
                item
                for item in query_requirements
                if item["id"].startswith(f"drilldown_{level_id}_")
            ]
            calculations.append(
                {
                    "id": result_id,
                    "type": CalculationOperation.DIFFERENCE.value,
                    "inputs": [item["id"] for item in level_queries],
                    "join_keys": list(_common_group_columns(level_queries)),
                    "value_columns": metric_names,
                }
            )
    else:
        result_ids = [item["id"] for item in query_requirements]

    primary_result_id = (
        f"drilldown_{spec.primary_level}_difference"
        if len(time_filters) == 2
        else f"drilldown_{spec.primary_level}_single"
    )
    supporting = [item for item in result_ids if item != primary_result_id]
    return {
        "query_requirements": query_requirements,
        "post_calculations": calculations,
        "result_contract": {
            "primary_requirement_id": primary_result_id,
            "supporting_requirement_ids": supporting,
            "ordered_requirement_ids": result_ids,
            "completion_policy": "require_primary",
            "analysis_type": "fixed_drilldown",
        },
    }


def _build_fixed_attribution_requirements(
    request: ModeRouteInput,
    schema: DatasetSchema,
    candidates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """展开加法指标的固定变化贡献归因。"""

    spec = request.semantic_parse.multi_step
    if spec is None or spec.type != "fixed_attribution":
        raise ModeRoutingError("SEMANTIC_FIXED_ATTRIBUTION_REQUIRED")
    metric = candidates[spec.metric_ref]
    dimension = candidates[spec.dimension_ref]
    if metric["model_id"] != dimension["model_id"]:
        raise ModeRoutingError("SEMANTIC_ATTRIBUTION_DIMENSION_MODEL_MISMATCH")
    metric_contract = next(
        (
            item
            for item in schema.metric_contracts
            if item.get("metric_id") == metric["asset_id"]
        ),
        None,
    )
    if not isinstance(metric_contract, dict) or metric_contract.get("additivity") != "FULL":
        raise ModeRoutingError("SEMANTIC_ATTRIBUTION_FULL_ADDITIVITY_REQUIRED")
    time_by_role = {item.role: item for item in request.semantic_parse.time_filters}
    if spec.current_time_role not in time_by_role or spec.previous_time_role not in time_by_role:
        raise ModeRoutingError("SEMANTIC_ATTRIBUTION_TIME_ROLES_REQUIRED")
    model_id = metric["model_id"]
    common_filters = [
        _filter_requirement(item, candidates[item.target_ref])
        for item in request.semantic_parse.filters
        if candidates[item.target_ref]["model_id"] == model_id
    ]
    query_requirements = []
    for scope_id, group_by in (("total", []), ("breakdown", [dimension])):
        for role in (spec.current_time_role, spec.previous_time_role):
            query_requirements.append(
                {
                    "id": f"attribution_{scope_id}_{role}",
                    "model_ref": f"MODEL:{model_id}",
                    "metrics": [metric],
                    "group_by": group_by,
                    "filters": common_filters,
                    "time": _time_requirement(
                        schema,
                        model_id,
                        time_by_role[role],
                        request.temporal_context,
                    ),
                    "order_by": [],
                    "limit": None,
                    "query_shape": {
                        "shape": "fixed_attribution",
                        "scope": scope_id,
                    },
                }
            )
    metric_name = metric["biz_name"]
    difference_column = f"{metric_name}_difference"
    calculations = [
        {
            "id": "attribution_total_difference",
            "type": CalculationOperation.DIFFERENCE.value,
            "inputs": [
                f"attribution_total_{spec.current_time_role}",
                f"attribution_total_{spec.previous_time_role}",
            ],
            "value_columns": [metric_name],
        },
        {
            "id": "attribution_breakdown_difference",
            "type": CalculationOperation.DIFFERENCE.value,
            "inputs": [
                f"attribution_breakdown_{spec.current_time_role}",
                f"attribution_breakdown_{spec.previous_time_role}",
            ],
            "join_keys": [dimension["column"]],
            "value_columns": [metric_name],
        },
        {
            "id": "attribution_contribution",
            "type": CalculationOperation.CONTRIBUTION.value,
            "inputs": [
                "attribution_breakdown_difference",
                "attribution_total_difference",
            ],
            "options": {
                "dimensions": [dimension["column"]],
                "difference_column": difference_column,
                "total_difference_column": difference_column,
                "output_column": f"{metric_name}_contribution",
                "reconciliation_tolerance": 1e-6,
            },
        },
    ]
    return {
        "query_requirements": query_requirements,
        "post_calculations": calculations,
        "result_contract": {
            "primary_requirement_id": "attribution_contribution",
            "supporting_requirement_ids": [],
            "ordered_requirement_ids": ["attribution_contribution"],
            "completion_policy": "require_primary",
            "analysis_type": "fixed_attribution",
        },
    }


def _asset_definition(element: SchemaElement) -> dict[str, Any]:
    """把 SchemaElement 转为执行需求使用的资产定义。"""

    if element.model is None:
        raise ModeRoutingError(f"SEMANTIC_ASSET_MODEL_REQUIRED:{element.id}")
    if element.type == "METRIC":
        expression = _metric_expression(element)
        if not expression:
            raise ModeRoutingError(f"SEMANTIC_METRIC_EXPRESSION_REQUIRED:{element.id}")
        return {
            "ref": f"METRIC:{element.id}:{element.model}",
            "model_ref": f"MODEL:{element.model}",
            "model_id": element.model,
            "asset_type": element.type,
            "asset_id": element.id,
            "display_name": element.name,
            "biz_name": element.biz_name,
            "expression": expression,
        }
    field = str(element.ext_info.get("field_name") or "").strip()
    if not field:
        raise ModeRoutingError(f"SEMANTIC_DIMENSION_FIELD_REQUIRED:{element.id}")
    return {
        "ref": f"DIMENSION:{element.id}:{element.model}",
        "model_ref": f"MODEL:{element.model}",
        "model_id": element.model,
        "asset_type": element.type,
        "asset_id": element.id,
        "display_name": element.name,
        "biz_name": element.biz_name,
        "column": field,
    }


def _metric_expression(element: SchemaElement) -> str:
    params = element.type_params
    define_type = str(params.get("metricDefineType") or "").upper()
    if define_type == "FIELD":
        field_params = params.get("metricDefineByFieldParams") or {}
        return str(field_params.get("expr") or "").strip()
    if define_type == "METRIC":
        metric_params = params.get("metricDefineByMetricParams") or {}
        return str(metric_params.get("expr") or "").strip()
    measure_params = params.get("metricDefineByMeasureParams") or {}
    raw_measures = (
        measure_params.get("measures") if isinstance(measure_params, dict) else []
    )
    measures = raw_measures if isinstance(raw_measures, list) else []
    expressions = [
        str(item.get("expr") or "").strip()
        for item in measures
        if isinstance(item, dict) and str(item.get("expr") or "").strip()
    ]
    return expressions[0] if len(expressions) == 1 else ""


def _filter_requirement(item: Any, target: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_ref": item.target_ref,
        "asset_id": target["asset_id"],
        "column": target.get("column"),
        "operator": item.operator,
        "value": item.value,
        "stage": item.stage,
    }


def _order_requirement(item: Any, target: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_ref": item.target_ref,
        "asset_id": target["asset_id"],
        "column": target.get("column") or target.get("expression"),
        "direction": item.direction,
    }


def _time_requirement(
    schema: DatasetSchema,
    model_id: int,
    time_filter: Any,
    temporal_context: TemporalContext | None = None,
) -> dict[str, Any] | None:
    if time_filter is None:
        return None
    time_dimension = next(
        (
            item
            for item in schema.dimensions
            if item.model == model_id and bool(item.ext_info.get("is_default_time"))
        ),
        None,
    )
    if time_dimension is None:
        raise ModeRoutingError(f"SEMANTIC_TIME_DIMENSION_REQUIRED:MODEL:{model_id}")
    column = str(time_dimension.ext_info.get("field_name") or "").strip()
    if not column:
        raise ModeRoutingError(f"SEMANTIC_TIME_FIELD_REQUIRED:{time_dimension.id}")
    requirement = {
        "role": time_filter.role,
        "expression": time_filter.expression,
        "dimension_ref": f"DIMENSION:{time_dimension.id}:{model_id}",
        "dimension_id": time_dimension.id,
        "column": column,
    }
    if temporal_context is not None:
        normalized = resolve_time_range(time_filter.expression, temporal_context)
        if not isinstance(normalized, dict) or normalized.get("kind") == "unsupported":
            raise ModeRoutingError("SEMANTIC_TIME_RANGE_UNRESOLVED")
        requirement["normalized"] = normalized
    return requirement


def _query_requirement_id(metrics: list[dict[str, Any]], role: str, index: int) -> str:
    metric_id = metrics[0]["asset_id"]
    return f"metric_{metric_id}_{role}_{index + 1}"


def _calculation_requirement(
    calculation: Any,
    query_requirements: list[dict[str, Any]],
) -> dict[str, Any]:
    details = dict(calculation.details)
    current_role = calculation.current_time_role or "current"
    previous_role = calculation.previous_time_role or "previous"
    current_inputs = [
        item["id"]
        for item in query_requirements
        if isinstance(item.get("time"), dict)
        and item["time"].get("role") == current_role
    ]
    previous_inputs = [
        item["id"]
        for item in query_requirements
        if isinstance(item.get("time"), dict)
        and item["time"].get("role") == previous_role
    ]
    inputs = [*current_inputs, *previous_inputs]
    if not inputs:
        inputs = [item["id"] for item in query_requirements]
    operation = CalculationOperation(calculation.type)
    if operation is CalculationOperation.RATIO:
        numerator = _metric_query_location(
            query_requirements,
            details.get("numerator_ref"),
        )
        denominator = _metric_query_location(
            query_requirements,
            details.get("denominator_ref"),
        )
        if numerator is None or denominator is None:
            raise ModeRoutingError("SEMANTIC_RATIO_METRICS_REQUIRED")
        inputs = list(dict.fromkeys((numerator[0], denominator[0])))
    if operation in {
        CalculationOperation.DIFFERENCE,
        CalculationOperation.GROWTH_RATE,
    } and (len(current_inputs) != 1 or len(previous_inputs) != 1):
        raise ModeRoutingError("SEMANTIC_PERIOD_CALCULATION_INPUTS_INVALID")
    join_keys = _common_group_columns(
        [item for item in query_requirements if item["id"] in inputs]
    )
    metric_names = tuple(
        str(item.get("biz_name") or item.get("display_name") or "").strip()
        for query in query_requirements
        if query["id"] in inputs
        for item in query["metrics"]
        if str(item.get("biz_name") or item.get("display_name") or "").strip()
    )
    value_columns = tuple(dict.fromkeys(metric_names))
    derive = tuple(
        item for item in details.get("derive") or [] if isinstance(item, dict)
    )
    if operation is CalculationOperation.RATIO and not derive:
        numerator = _metric_query_location(
            query_requirements,
            details.get("numerator_ref"),
        )
        denominator = _metric_query_location(
            query_requirements,
            details.get("denominator_ref"),
        )
        if numerator is None or denominator is None:
            raise ModeRoutingError("SEMANTIC_RATIO_METRICS_REQUIRED")
        numerator_expr = (
            numerator[1] if len(inputs) == 1 else f'{numerator[0]}."{numerator[1]}"'
        )
        denominator_expr = (
            denominator[1]
            if len(inputs) == 1
            else f'{denominator[0]}."{denominator[1]}"'
        )
        derive = (
            {
                "name": str(details.get("result_name") or "ratio"),
                "expr": f"{numerator_expr} / NULLIF({denominator_expr}, 0)",
            },
        )
    options = {
        key: value
        for key, value in details.items()
        if key
        in {
            "dimensions",
            "dimension",
            "top_n",
            "index",
            "columns",
            "column",
        }
    }
    if value_columns:
        options["value_columns"] = list(value_columns)
    return {
        "id": str(details.get("result_name") or calculation.type),
        "type": operation.value,
        "inputs": inputs,
        "join_keys": list(join_keys),
        "value_columns": list(value_columns),
        "derive": list(derive),
        "options": options,
    }


def _merge_requirement(query_requirements: list[dict[str, Any]]) -> dict[str, Any]:
    """多查询共同回答问题时生成明确合并节点，禁止默认选择首个结果。"""

    return {
        "id": "merge_results",
        "type": CalculationOperation.MERGE.value,
        "inputs": [item["id"] for item in query_requirements],
        "join_keys": list(_common_group_columns(query_requirements)),
    }


def _metric_query_location(
    query_requirements: list[dict[str, Any]],
    metric_ref: Any,
) -> tuple[str, str] | None:
    """返回指标所在查询和稳定结果字段名。"""

    if not isinstance(metric_ref, str) or not metric_ref:
        return None
    for query in query_requirements:
        for metric in query.get("metrics") or []:
            if metric.get("ref") != metric_ref:
                continue
            field = str(
                metric.get("biz_name") or metric.get("display_name") or ""
            ).strip()
            return (str(query["id"]), field) if field else None
    return None


def _common_group_columns(
    query_requirements: list[dict[str, Any]],
) -> tuple[str, ...]:
    """只使用全部输入查询共有的业务维度列作为结果连接键。"""

    if not query_requirements:
        return ()
    column_sets = [
        {
            str(item.get("column"))
            for item in query.get("group_by") or []
            if item.get("column")
        }
        for query in query_requirements
    ]
    common = set.intersection(*column_sets) if column_sets else set()
    return tuple(sorted(common))


def _normalize_modes(modes: tuple[str, ...] | list[str] | str) -> set[str]:
    values = modes.split(",") if isinstance(modes, str) else modes
    normalized = {_normalize_mode(item) for item in values}
    return {item for item in normalized if item is not None}


def _normalize_mode(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized not in {
        AgentExecutionMode.FAST.value,
        AgentExecutionMode.PLAN.value,
        AgentExecutionMode.RESEARCH.value,
    }:
        raise ModeRoutingError(f"EXECUTION_MODE_INVALID:{normalized}")
    return normalized


__all__ = ["ModeRouteInput", "ModeRouter", "ModeRoutingError"]
