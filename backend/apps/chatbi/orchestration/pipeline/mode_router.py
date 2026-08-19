"""补充资产定义、生成执行需求并选择 Fast 或 Plan。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chatbi.models.dto.execution_requirement import ExecutionRequirement
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
    requested_mode: str | None = None
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
        selected_refs = _selected_refs(semantic_parse)
        candidates = self._resolve_candidates(request, schema, set(selected_refs))
        missing_refs = sorted(set(selected_refs) - set(candidates))
        if missing_refs:
            raise ModeRoutingError(
                "SEMANTIC_PARSE_CANDIDATE_NOT_FOUND:" + ",".join(missing_refs)
            )

        execution = self._build_execution_requirements(request, schema, candidates)
        mode, reasons = self._select_mode(execution)
        requested = _normalize_mode(request.requested_mode)
        if requested is not None:
            if requested not in enabled:
                raise ModeRoutingError(f"EXECUTION_MODE_NOT_ENABLED:{requested}")
            if requested == AgentExecutionMode.FAST.value and mode != AgentExecutionMode.FAST:
                raise ModeRoutingError("FAST_ROUTE_INCOMPATIBLE_WITH_EXECUTION_REQUIREMENT")
            mode = AgentExecutionMode(requested)
            reasons = ["requested_mode", *reasons]

        if mode.value not in enabled:
            raise ModeRoutingError(f"EXECUTION_MODE_NOT_AVAILABLE:{mode.value}")


        return ExecutionRequirement(
            status="ready",
            route={
                "mode": mode.value,
                "reasons": tuple(dict.fromkeys(reasons)),
            },
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

        time_filters = semantic_parse.time_filters or [None]
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
        covered_refs.update(item["target_ref"] for query in query_requirements for item in query["filters"])
        covered_refs.update(item["target_ref"] for query in query_requirements for item in query["order_by"])
        missing_execution_refs = sorted(set(selected_refs) - covered_refs)
        if missing_execution_refs:
            raise ModeRoutingError(
                "SEMANTIC_EXECUTION_ASSET_NOT_COVERED:"
                + ",".join(missing_execution_refs)
            )

        post_calculations = [
            _calculation_requirement(item, query_requirements, metric_refs)
            for item in semantic_parse.calculations
        ]
        return {
            "query_requirements": query_requirements,
            "post_calculations": post_calculations,
        }

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
        if not reasons:
            return AgentExecutionMode.FAST, ["single_model", "single_query"]
        return AgentExecutionMode.PLAN, reasons


def _selected_refs(semantic_parse: SemanticParseOutput) -> list[str]:
    return [
        *(item.ref for item in semantic_parse.measures),
        *(item.ref for item in semantic_parse.group_by),
        *(item.target_ref for item in semantic_parse.filters),
        *(item.target_ref for item in semantic_parse.order_by),
    ]


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
    measures = measure_params.get("measures") if isinstance(measure_params, dict) else []
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
            if item.model == model_id
            and bool(item.ext_info.get("is_default_time"))
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
    metric_refs: list[str],
) -> dict[str, Any]:
    details = dict(calculation.details)
    metric_ref = details.get("metric_ref") or (metric_refs[0] if metric_refs else None)
    current_role = calculation.current_time_role or "current"
    previous_role = calculation.previous_time_role or "previous"
    inputs = [
        item["id"]
        for item in query_requirements
        if isinstance(item.get("time"), dict)
        and item["time"].get("role") in {current_role, previous_role}
    ]
    if not inputs:
        inputs = [item["id"] for item in query_requirements]
    return {
        "id": str(details.get("result_name") or calculation.type),
        "type": calculation.type,
        "metric_ref": metric_ref,
        "inputs": inputs,
        "current_time_role": current_role,
        "previous_time_role": previous_role,
        "details": details,
    }


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
    }:
        raise ModeRoutingError(f"EXECUTION_MODE_INVALID:{normalized}")
    return normalized


__all__ = ["ModeRouteInput", "ModeRouter", "ModeRoutingError"]
