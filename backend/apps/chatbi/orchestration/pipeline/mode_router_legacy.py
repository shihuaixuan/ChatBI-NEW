"""补充资产定义、生成执行需求并选择 Fast 或 Plan。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from apps.chatbi.errors import (
    LimitedMultiStepDecompositionError,
    ResearchRequirementError,
)
from apps.chatbi.models.dto.execution_requirement import (
    CalculationOperation,
    DecompositionCalculationDraft,
    ExecutionRequirement,
    ExecutionRoute,
)
from apps.chatbi.models.dto.research_agent import ResearchBudget
from apps.chatbi.models.dto.semantic_parse import (
    SemanticParseAssetRef,
    SemanticParseOutput,
    SemanticParseTimeFilter,
)
from apps.chatbi.models.orm.agent_run import AgentExecutionMode
from apps.chatbi.services.planning.limited_multistep import LimitedMultiStepDecomposer
from apps.chatbi.services.research.routing_freeze import freeze_research_requirement
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
    user_id: int | None = None
    permission_version: str | None = None
    authorized_tables: tuple[str, ...] = ()
    research_budget: ResearchBudget | None = None


class ModeRouter:
    """根据语义解析结果和候选资产生成执行需求。"""

    def __init__(
        self,
        schema_provider: DatasetSchemaProvider,
        limited_multistep_decomposer: LimitedMultiStepDecomposer | None = None,
    ) -> None:
        if schema_provider is None:
            raise ValueError("MODE_ROUTER_SCHEMA_PROVIDER_REQUIRED")
        self._schema_provider = schema_provider
        self._limited_multistep_decomposer = limited_multistep_decomposer

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
        dynamic_research = (
            semantic_parse.multi_step
            if semantic_parse.multi_step is not None
            and semantic_parse.multi_step.type == "dynamic_research"
            else None
        )
        selected_refs = _selected_refs(semantic_parse)
        candidates = self._resolve_candidates(request, schema, set(selected_refs))
        missing_refs = sorted(set(selected_refs) - set(candidates))
        if missing_refs:
            raise ModeRoutingError(
                "SEMANTIC_PARSE_CANDIDATE_NOT_FOUND:" + ",".join(missing_refs)
            )
        complete_dynamic_parse = _complete_dynamic_plan_parse(
            semantic_parse,
            candidates,
        )
        if dynamic_research is not None and complete_dynamic_parse is None:
            if AgentExecutionMode.RESEARCH.value not in enabled:
                raise ModeRoutingError("EXECUTION_MODE_NOT_AVAILABLE:research")
            try:
                research_requirement = freeze_research_requirement(
                    semantic_parse=semantic_parse,
                    schema=schema,
                    temporal_context=request.temporal_context,
                    budget=request.research_budget,
                    tenant_scope=f"oid:{int(request.tenant_id)}",
                    dataset_ref=f"ASSET:dataset:{request.dataset_id or 0}",
                    user_id=request.user_id,
                    datasource_id=request.datasource_id,
                    permission_version=request.permission_version,
                    authorized_tables=request.authorized_tables,
                )
            except ResearchRequirementError as exc:
                raise ModeRoutingError(exc.code) from exc
            except ValueError as exc:
                # 新契约字段校验失败（如无时间过滤的问题无法满足时间绑定），
                # 与旧路径投影失败同口径显式暴露。
                raise ModeRoutingError(f"RESEARCH_REQUIREMENT_INVALID:{exc}") from exc
            research_refs = {
                *research_requirement.target_metric_refs,
                *research_requirement.scope.dimension_refs,
                *research_requirement.scope.driver_metric_refs,
                *(item.target_ref for item in research_requirement.immutable_filters),
            }
            schema_elements = {
                **{f"METRIC:{item.id}:{item.model}": item for item in schema.metrics},
                **{
                    f"DIMENSION:{item.id}:{item.model}": item
                    for item in schema.dimensions
                },
            }
            research_assets = {
                ref: {
                    **_asset_definition(schema_elements[ref]),
                    "description": str(schema_elements[ref].description or ""),
                }
                for ref in sorted(research_refs)
                if ref in schema_elements
            }
            missing_research_assets = sorted(research_refs - set(research_assets))
            if missing_research_assets:
                raise ModeRoutingError(
                    "RESEARCH_ASSET_SNAPSHOT_INCOMPLETE:"
                    + ",".join(missing_research_assets)
                )
            time_assets = {
                item.dimension_ref: {
                    **_asset_definition(schema_elements[item.dimension_ref]),
                    "description": str(
                        schema_elements[item.dimension_ref].description or ""
                    ),
                }
                for item in research_requirement.time_bindings
                if item.dimension_ref in schema_elements
            }
            research_assets.update(time_assets)
            for bindings in research_requirement.time_bindings_by_model.values():
                for item in bindings:
                    if item.dimension_ref not in schema_elements:
                        continue
                    research_assets[item.dimension_ref] = {
                        **_asset_definition(schema_elements[item.dimension_ref]),
                        "description": str(
                            schema_elements[item.dimension_ref].description or ""
                        ),
                    }
            return ExecutionRequirement(
                status="ready",
                route=ExecutionRoute(
                    mode=AgentExecutionMode.RESEARCH.value,
                    reasons=(
                        "dynamic_research",
                        dynamic_research.reason,
                    ),
                ),
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
                    # Research 子计划必须持续使用启动时已发布的不可变 Schema。
                    "dataset_schema": schema.model_dump(mode="json"),
                    # 执行资产保留在服务端快照中，Research Policy 只读取清理后的逻辑目录。
                    "research_assets": research_assets,
                },
                research_requirement=research_requirement.model_dump(mode="json"),
            ).model_dump(mode="json")

        # 动态语义只有在结构化输入不足以证明固定拓扑时才保留 Research。
        # 已经明确的驱动指标和分析维度先进入普通执行需求，再由执行节点结构
        # 判断 Fast 或 Plan，避免把“需要验证主要原因”误当成动态路由依据。
        if complete_dynamic_parse is not None:
            request = replace(request, semantic_parse=complete_dynamic_parse)
        execution = self._build_execution_requirements(request, schema, candidates)
        mode, reasons = self._select_mode(execution)
        if dynamic_research is not None:
            reasons = ["complete_initial_plan", *reasons]
        if mode.value not in enabled:
            raise ModeRoutingError(f"EXECUTION_MODE_NOT_AVAILABLE:{mode.value}")

        scope_fingerprint = _analysis_scope_fingerprint(
            execution,
            schema_fingerprint=schema.schema_fingerprint,
        )
        permission_fingerprint = _analysis_permission_fingerprint(request)

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
                "scope_fingerprint": scope_fingerprint,
                "permission_fingerprint": permission_fingerprint,
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

        # 普通执行需求只会把维度挂到同模型指标查询上。此前跨模型维度会被
        # 静默跳过，直到最终覆盖检查才暴露内部错误码；这里在生成查询前按
        # 实际执行能力明确拒绝，不能把“未生成查询”误报成“资产未覆盖”。
        metric_model_ids = {
            candidates[ref]["model_id"]
            for ref in metric_refs
            if candidates[ref].get("model_id") is not None
        }
        selected_dimension_refs = {
            *dimension_refs,
            *(item.target_ref for item in semantic_parse.filters),
            *(
                item.target_ref
                for item in semantic_parse.order_by
                if candidates[item.target_ref]["asset_type"] == "DIMENSION"
            ),
        }
        incompatible_dimensions = sorted(
            ref
            for ref in selected_dimension_refs
            if candidates[ref]["model_id"] not in metric_model_ids
        )
        if incompatible_dimensions:
            raise ModeRoutingError(
                "SEMANTIC_METRIC_DIMENSION_INCOMPATIBLE:"
                + ",".join(incompatible_dimensions)
            )

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
        if len(post_calculations) > 1:
            # 同一问题明确要求的并列计算属于同级输出，统一合并为一个最终
            # 结果集，避免为了满足执行契约而人为指定主结果和辅助结果。
            post_calculations.append(
                _merge_parallel_calculation_results(post_calculations)
            )
        elif len(query_requirements) > 1 and not post_calculations:
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
        if multi_step.type == "limited_multistep":
            if self._limited_multistep_decomposer is None:
                raise ModeRoutingError("LIMITED_MULTISTEP_DECOMPOSER_REQUIRED")
            return _build_limited_multistep_requirements(
                request,
                schema,
                candidates,
                self._limited_multistep_decomposer,
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
            if analysis_type in {"fixed_attribution", "limited_multistep"} or (
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
    elif multi_step is not None and multi_step.type == "limited_multistep":
        refs.extend(multi_step.metric_refs)
        refs.extend(multi_step.dimension_refs)
    elif multi_step is not None and multi_step.type == "dynamic_research":
        refs.extend(multi_step.required_dimension_refs)
        refs.extend(multi_step.required_driver_metric_refs)
    return list(dict.fromkeys(refs))


def _complete_dynamic_plan_parse(
    semantic_parse: SemanticParseOutput,
    candidates: dict[str, dict[str, Any]],
) -> SemanticParseOutput | None:
    """把已声明完整拓扑的动态语义投影为固定执行语义。"""

    dynamic = semantic_parse.multi_step
    if dynamic is None or dynamic.type != "dynamic_research":
        return None
    # 这些原因明确表示后续节点类型或停止条件依赖结果，当前路由器不能
    # 在没有 AnalysisPlan 证明的情况下把它们降级为固定计划。
    if dynamic.reason.value in {
        "result_driven_filter",
        "result_driven_dimension",
        "data_driven_stop_condition",
    }:
        return None

    metric_refs = list(dict.fromkeys(
        [item.ref for item in semantic_parse.measures]
        + list(dynamic.required_driver_metric_refs)
    ))
    dimension_refs = list(dict.fromkeys(
        [item.ref for item in semantic_parse.group_by]
        + list(dynamic.required_dimension_refs)
    ))
    if (
        not metric_refs
        or not dynamic.required_driver_metric_refs
        or not dimension_refs
    ):
        return None
    if any(
        ref not in candidates
        or candidates[ref].get("asset_type") != "METRIC"
        for ref in metric_refs
    ):
        raise ModeRoutingError("SEMANTIC_FIXED_PLAN_METRIC_BINDING_REQUIRED")
    if any(
        ref not in candidates
        or candidates[ref].get("asset_type") != "DIMENSION"
        for ref in dimension_refs
    ):
        raise ModeRoutingError("SEMANTIC_FIXED_PLAN_DIMENSION_BINDING_REQUIRED")
    model_ids = {
        candidates[ref].get("model_id")
        for ref in (*metric_refs, *dimension_refs)
    }
    if len(model_ids) != 1 or None in model_ids:
        return None

    # 只保留已绑定资产和原有结构化计算，动态目标文本不会进入执行器。
    return semantic_parse.model_copy(
        update={
            "measures": [
                SemanticParseAssetRef(ref=ref)
                for ref in metric_refs
            ],
            "group_by": [
                SemanticParseAssetRef(ref=ref)
                for ref in dimension_refs
            ],
            "multi_step": None,
        }
    )


@dataclass(frozen=True, slots=True)
class _LimitedOutputShape:
    """草案物化期间跟踪逻辑资产对应的实际结果字段。"""

    metric_fields: dict[str, str]
    dimension_columns: dict[str, str]


def _build_limited_multistep_requirements(
    request: ModeRouteInput,
    schema: DatasetSchema,
    candidates: dict[str, dict[str, Any]],
    decomposer: LimitedMultiStepDecomposer,
) -> dict[str, Any]:
    """调用一次受限模型分解，并用权威资产补齐正式执行需求。"""

    spec = request.semantic_parse.multi_step
    if spec is None or spec.type != "limited_multistep":
        raise ModeRoutingError("SEMANTIC_LIMITED_MULTISTEP_REQUIRED")
    if len(spec.allowed_time_roles) != len(set(spec.allowed_time_roles)):
        raise ModeRoutingError("SEMANTIC_LIMITED_MULTISTEP_TIME_ROLE_DUPLICATED")
    semantic_time_roles = tuple(item.role for item in request.semantic_parse.time_filters)
    expected_time_roles = semantic_time_roles or ("single",)
    if set(spec.allowed_time_roles) != set(expected_time_roles):
        raise ModeRoutingError("SEMANTIC_LIMITED_MULTISTEP_TIME_ROLES_INVALID")
    selected_metric_refs = set(spec.metric_refs)
    selected_dimension_refs = set(spec.dimension_refs)
    if not selected_metric_refs <= set(candidates):
        raise ModeRoutingError("SEMANTIC_LIMITED_MULTISTEP_METRIC_BINDING_REQUIRED")
    if not selected_dimension_refs <= set(candidates):
        raise ModeRoutingError("SEMANTIC_LIMITED_MULTISTEP_DIMENSION_BINDING_REQUIRED")

    metric_contracts = {
        int(item["metric_id"]): item
        for item in schema.metric_contracts
        if isinstance(item, dict) and isinstance(item.get("metric_id"), int)
    }
    available_metrics = [
        {
            "ref": ref,
            "display_name": candidates[ref]["display_name"],
            "biz_name": candidates[ref]["biz_name"],
            "model_ref": candidates[ref]["model_ref"],
            "additivity": metric_contracts.get(
                int(candidates[ref]["asset_id"]), {}
            ).get("additivity"),
        }
        for ref in spec.metric_refs
    ]
    available_dimensions = [
        {
            "ref": ref,
            "display_name": candidates[ref]["display_name"],
            "biz_name": candidates[ref]["biz_name"],
            "model_ref": candidates[ref]["model_ref"],
        }
        for ref in spec.dimension_refs
    ]
    allowed_operations = (
        CalculationOperation.MERGE,
        CalculationOperation.DIFFERENCE,
        CalculationOperation.GROWTH_RATE,
        CalculationOperation.SHARE,
        CalculationOperation.RATIO,
        CalculationOperation.TOPN_OTHER,
        CalculationOperation.PIVOT,
        CalculationOperation.CONTRIBUTION,
    )
    try:
        decomposition = decomposer.decompose(
            objective=spec.objective,
            available_metrics=available_metrics,
            available_dimensions=available_dimensions,
            time_roles=spec.allowed_time_roles,
            requested_outputs=spec.requested_outputs,
            allowed_operations=allowed_operations,
        )
    except LimitedMultiStepDecompositionError as exc:
        raise ModeRoutingError(exc.code) from exc
    for calculation in decomposition.draft.post_calculations:
        if calculation.type is not CalculationOperation.CONTRIBUTION:
            continue
        if any(
            metric_contracts.get(int(candidates[ref]["asset_id"]), {}).get(
                "additivity"
            )
            != "FULL"
            for ref in calculation.metric_refs
        ):
            raise ModeRoutingError("LIMITED_MULTISTEP_CONTRIBUTION_FULL_REQUIRED")
    time_by_role = {
        item.role: item for item in request.semantic_parse.time_filters
    }
    query_requirements: list[dict[str, Any]] = []
    shapes: dict[str, _LimitedOutputShape] = {}
    for query in decomposition.draft.query_requirements:
        metric_defs = [candidates[ref] for ref in query.metric_refs]
        dimension_defs = [candidates[ref] for ref in query.dimension_refs]
        model_ids = {
            item["model_id"] for item in (*metric_defs, *dimension_defs)
        }
        if len(model_ids) != 1:
            raise ModeRoutingError("LIMITED_MULTISTEP_QUERY_SINGLE_MODEL_REQUIRED")
        model_id = next(iter(model_ids))
        time_filter = time_by_role.get(query.time_role)
        if query.time_role != "single" and time_filter is None:
            raise ModeRoutingError("LIMITED_MULTISTEP_TIME_ROLE_NOT_BOUND")
        filters = [
            _filter_requirement(item, candidates[item.target_ref])
            for item in request.semantic_parse.filters
            if candidates[item.target_ref]["model_id"] == model_id
        ]
        query_requirements.append(
            {
                "id": query.id,
                "model_ref": f"MODEL:{model_id}",
                "metrics": metric_defs,
                "group_by": dimension_defs,
                "filters": filters,
                "time": _time_requirement(
                    schema,
                    model_id,
                    time_filter,
                    request.temporal_context,
                ),
                "order_by": [],
                "limit": None,
                "query_shape": {"shape": "limited_multistep"},
            }
        )
        shapes[query.id] = _LimitedOutputShape(
            metric_fields={item["ref"]: item["biz_name"] for item in metric_defs},
            dimension_columns={
                item["ref"]: item["column"] for item in dimension_defs
            },
        )

    calculations: list[dict[str, Any]] = []
    for calculation in _ordered_limited_calculations(
        decomposition.draft.post_calculations,
        set(shapes),
    ):
        requirement, shape = _materialize_limited_calculation(
            calculation,
            shapes,
            candidates,
        )
        calculations.append(requirement)
        shapes[calculation.id] = shape
    return {
        "query_requirements": query_requirements,
        "post_calculations": calculations,
        "result_contract": decomposition.draft.result_contract.model_dump(mode="json"),
        "decomposition": decomposition.audit.model_dump(mode="json"),
    }


def _ordered_limited_calculations(
    calculations: tuple[DecompositionCalculationDraft, ...],
    available_ids: set[str],
) -> tuple[DecompositionCalculationDraft, ...]:
    """按模型原始顺序稳定生成计算节点的拓扑序。"""

    pending = list(calculations)
    ordered: list[DecompositionCalculationDraft] = []
    resolved_ids = set(available_ids)
    while pending:
        ready = [item for item in pending if set(item.inputs) <= resolved_ids]
        if not ready:
            raise ModeRoutingError("LIMITED_MULTISTEP_CALCULATION_ORDER_INVALID")
        for item in ready:
            pending.remove(item)
            ordered.append(item)
            resolved_ids.add(item.id)
    return tuple(ordered)


def _materialize_limited_calculation(
    calculation: DecompositionCalculationDraft,
    shapes: dict[str, _LimitedOutputShape],
    candidates: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], _LimitedOutputShape]:
    """把模型草案计算转换为现有白名单计算契约。"""

    input_shapes = [shapes[item] for item in calculation.inputs]
    common_dimensions = _common_limited_dimensions(input_shapes)
    requirement: dict[str, Any] = {
        "id": calculation.id,
        "type": calculation.type.value,
        "inputs": list(calculation.inputs),
        "join_keys": list(common_dimensions.values()),
    }
    if calculation.type is CalculationOperation.MERGE:
        metric_fields: dict[str, str] = {}
        for shape in input_shapes:
            duplicated = set(metric_fields) & set(shape.metric_fields)
            if duplicated:
                raise ModeRoutingError("LIMITED_MULTISTEP_MERGE_METRIC_DUPLICATED")
            metric_fields.update(shape.metric_fields)
        return requirement, _LimitedOutputShape(metric_fields, common_dimensions)

    if calculation.type in {
        CalculationOperation.DIFFERENCE,
        CalculationOperation.GROWTH_RATE,
    }:
        value_columns = [
            _same_metric_field(input_shapes, ref) for ref in calculation.metric_refs
        ]
        suffix = (
            "difference"
            if calculation.type is CalculationOperation.DIFFERENCE
            else "growth_rate"
        )
        requirement["value_columns"] = value_columns
        return requirement, _LimitedOutputShape(
            {
                ref: f"{field}_{suffix}"
                for ref, field in zip(
                    calculation.metric_refs,
                    value_columns,
                    strict=True,
                )
            },
            common_dimensions,
        )

    if calculation.type is CalculationOperation.SHARE:
        metric_ref = calculation.metric_refs[0]
        metric_field = _limited_metric_field(input_shapes[0], metric_ref)
        dimensions = _limited_dimension_columns(
            input_shapes[0], calculation.dimension_refs
        )
        requirement["value_columns"] = [metric_field]
        requirement["options"] = {"dimensions": list(dimensions)}
        return requirement, _LimitedOutputShape(
            {metric_ref: f"{metric_field}_share"},
            dict(
                zip(
                    calculation.dimension_refs,
                    dimensions,
                    strict=True,
                )
            ),
        )

    if calculation.type is CalculationOperation.TOPN_OTHER:
        metric_ref = calculation.metric_refs[0]
        dimension_ref = calculation.dimension_refs[0]
        metric_field = _limited_metric_field(input_shapes[0], metric_ref)
        dimension_column = _limited_dimension_column(
            input_shapes[0], dimension_ref
        )
        requirement["value_columns"] = [metric_field]
        requirement["options"] = {
            "dimension": dimension_column,
            "top_n": calculation.top_n,
        }
        return requirement, _LimitedOutputShape(
            {metric_ref: "metric_value"},
            {dimension_ref: "dimension_value"},
        )

    if calculation.type is CalculationOperation.RATIO:
        if calculation.numerator_ref is None or calculation.denominator_ref is None:
            raise ModeRoutingError("LIMITED_MULTISTEP_RATIO_REFS_REQUIRED")
        numerator_input, numerator_field = _find_limited_metric_input(
            calculation.inputs,
            input_shapes,
            calculation.numerator_ref,
        )
        denominator_input, denominator_field = _find_limited_metric_input(
            calculation.inputs,
            input_shapes,
            calculation.denominator_ref,
        )
        numerator_expr = (
            numerator_field
            if len(calculation.inputs) == 1
            else f'{numerator_input}."{numerator_field}"'
        )
        denominator_expr = (
            denominator_field
            if len(calculation.inputs) == 1
            else f'{denominator_input}."{denominator_field}"'
        )
        requirement["derive"] = [
            {
                "name": calculation.result_name,
                "expr": f"{numerator_expr} / NULLIF({denominator_expr}, 0)",
            }
        ]
        return requirement, _LimitedOutputShape({}, common_dimensions)

    if calculation.type is CalculationOperation.PIVOT:
        metric_ref = calculation.metric_refs[0]
        metric_field = _limited_metric_field(input_shapes[0], metric_ref)
        index_columns = _limited_dimension_columns(
            input_shapes[0], calculation.index_dimension_refs
        )
        if calculation.column_dimension_ref is None:
            raise ModeRoutingError("LIMITED_MULTISTEP_PIVOT_COLUMN_REQUIRED")
        column = _limited_dimension_column(
            input_shapes[0], calculation.column_dimension_ref
        )
        requirement["value_columns"] = [metric_field]
        requirement["options"] = {
            "index": list(index_columns),
            "column": column,
        }
        return requirement, _LimitedOutputShape({}, {})

    if calculation.type is CalculationOperation.CONTRIBUTION:
        metric_ref = calculation.metric_refs[0]
        breakdown_shape, total_shape = input_shapes
        difference_column = _limited_metric_field(breakdown_shape, metric_ref)
        total_difference_column = _limited_metric_field(total_shape, metric_ref)
        dimensions = _limited_dimension_columns(
            breakdown_shape, calculation.dimension_refs
        )
        output_column = f"{candidates[metric_ref]['biz_name']}_contribution"
        requirement["options"] = {
            "dimensions": list(dimensions),
            "difference_column": difference_column,
            "total_difference_column": total_difference_column,
            "output_column": output_column,
            "reconciliation_tolerance": 1e-6,
        }
        return requirement, _LimitedOutputShape(
            {metric_ref: output_column},
            dict(
                zip(
                    calculation.dimension_refs,
                    dimensions,
                    strict=True,
                )
            ),
        )
    raise ModeRoutingError("LIMITED_MULTISTEP_CALCULATION_UNSUPPORTED")


def _common_limited_dimensions(
    shapes: list[_LimitedOutputShape],
) -> dict[str, str]:
    if not shapes:
        return {}
    common_refs = set(shapes[0].dimension_columns)
    for shape in shapes[1:]:
        common_refs &= set(shape.dimension_columns)
    result: dict[str, str] = {}
    for ref in shapes[0].dimension_columns:
        if ref not in common_refs:
            continue
        columns = {shape.dimension_columns[ref] for shape in shapes}
        if len(columns) != 1:
            raise ModeRoutingError("LIMITED_MULTISTEP_DIMENSION_COLUMN_MISMATCH")
        result[ref] = next(iter(columns))
    return result


def _same_metric_field(
    shapes: list[_LimitedOutputShape],
    metric_ref: str,
) -> str:
    fields = {_limited_metric_field(shape, metric_ref) for shape in shapes}
    if len(fields) != 1:
        raise ModeRoutingError("LIMITED_MULTISTEP_METRIC_FIELD_MISMATCH")
    return next(iter(fields))


def _limited_metric_field(shape: _LimitedOutputShape, metric_ref: str) -> str:
    field = shape.metric_fields.get(metric_ref)
    if not field:
        raise ModeRoutingError(f"LIMITED_MULTISTEP_METRIC_OUTPUT_REQUIRED:{metric_ref}")
    return field


def _limited_dimension_column(
    shape: _LimitedOutputShape,
    dimension_ref: str,
) -> str:
    column = shape.dimension_columns.get(dimension_ref)
    if not column:
        raise ModeRoutingError(
            f"LIMITED_MULTISTEP_DIMENSION_OUTPUT_REQUIRED:{dimension_ref}"
        )
    return column


def _limited_dimension_columns(
    shape: _LimitedOutputShape,
    dimension_refs: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(_limited_dimension_column(shape, ref) for ref in dimension_refs)


def _find_limited_metric_input(
    input_ids: tuple[str, ...],
    shapes: list[_LimitedOutputShape],
    metric_ref: str,
) -> tuple[str, str]:
    matches = [
        (input_id, shape.metric_fields[metric_ref])
        for input_id, shape in zip(input_ids, shapes, strict=True)
        if metric_ref in shape.metric_fields
    ]
    if len(matches) != 1 and len(input_ids) > 1:
        raise ModeRoutingError(f"LIMITED_MULTISTEP_RATIO_INPUT_AMBIGUOUS:{metric_ref}")
    if not matches:
        raise ModeRoutingError(f"LIMITED_MULTISTEP_METRIC_OUTPUT_REQUIRED:{metric_ref}")
    return matches[0]


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

    time_filters: list[SemanticParseTimeFilter | None] = list(
        request.semantic_parse.time_filters
    ) or [None]
    if len(time_filters) not in {1, 2}:
        raise ModeRoutingError("SEMANTIC_DRILLDOWN_TIME_COUNT_UNSUPPORTED")
    if len(time_filters) == 2 and {
        item.role for item in time_filters if item is not None
    } != {
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
        raise ModeRoutingError(
            "SEMANTIC_METRIC_DIMENSION_INCOMPATIBLE:" + spec.dimension_ref
        )
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
    if not isinstance(measure_params, dict):
        return ""
    raw_measures = measure_params.get("measures")
    measures = raw_measures if isinstance(raw_measures, list) else []
    expressions = [
        str(item.get("expr") or "").strip()
        for item in measures
        if isinstance(item, dict) and str(item.get("expr") or "").strip()
    ]
    if len(expressions) == 1:
        return expressions[0]
    # 派生指标（如客单价）只有顶层聚合表达式、无底层 measures 列表，
    # 与 SemanticSQLCompiler._metric_measure_expr 的取数逻辑保持一致。
    return str(measure_params.get("expr") or "").strip()


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


def _merge_parallel_calculation_results(
    calculations: list[dict[str, Any]],
) -> dict[str, Any]:
    """把同级计算结果合并为唯一最终结果，保持用户要求的计算顺序。"""

    if len(calculations) < 2:
        raise ModeRoutingError("SEMANTIC_PARALLEL_CALCULATIONS_REQUIRED")
    join_keys = tuple(calculations[0].get("join_keys") or ())
    if any(
        tuple(item.get("join_keys") or ()) != join_keys
        for item in calculations[1:]
    ):
        # 不同粒度的结果不能直接合并，否则可能产生扇出或错误对齐。
        raise ModeRoutingError("SEMANTIC_CALCULATION_RESULT_GRAIN_MISMATCH")
    known_ids = {str(item["id"]) for item in calculations}
    result_id = "combined_results"
    suffix = 2
    while result_id in known_ids:
        result_id = f"combined_results_{suffix}"
        suffix += 1
    return {
        "id": result_id,
        "type": CalculationOperation.MERGE.value,
        "inputs": [str(item["id"]) for item in calculations],
        "join_keys": list(join_keys),
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


def _analysis_scope_fingerprint(
    execution: dict[str, Any],
    *,
    schema_fingerprint: str,
) -> str:
    """冻结 Fast/Plan 实际可执行需求，作为统一 Evidence 的 Scope 版本。"""

    return _fingerprint(
        {
            "schema_fingerprint": schema_fingerprint,
            "query_requirements": execution.get("query_requirements") or (),
            "post_calculations": execution.get("post_calculations") or (),
            "result_contract": execution.get("result_contract"),
        }
    )


def _analysis_permission_fingerprint(request: ModeRouteInput) -> str:
    """冻结本次执行使用的租户、用户、数据源和授权表边界。"""

    return _fingerprint(
        {
            "tenant_id": request.tenant_id,
            "user_id": request.user_id,
            "datasource_id": request.datasource_id,
            "dataset_id": request.dataset_id,
            "permission_version": request.permission_version,
            "authorized_tables": sorted(set(request.authorized_tables)),
        }
    )


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


__all__ = ["ModeRouteInput", "ModeRouter", "ModeRoutingError"]
