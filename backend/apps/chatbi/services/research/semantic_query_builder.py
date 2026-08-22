"""把 Research Semantic Query 编译为受治理的分析执行规格。"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from typing import Any

from apps.chatbi.errors import SemanticQueryBuildError
from apps.chatbi.models.dto.execution_requirement import (
    AnalysisExecutionSpec,
    CalculationOperation,
    CalculationRequirement,
    ExecutionResultContract,
    QueryRequirement,
)
from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchEvidence,
    ResearchEvidenceValueRef,
    ResearchQueryComparison,
    ResearchSemanticQuery,
    ResearchTimeBinding,
    ResearchTimeRole,
)
from apps.tool.tools.semantic_contracts import SemanticAssetScope


class SemanticQueryBuilder:
    """只使用已冻结的语义资产构造执行规格，不补充自然语言语义。"""

    def __init__(
        self,
        semantic_scope: SemanticAssetScope | None = None,
        schema_snapshot: Any | None = None,
        evidence_value_resolver: Callable[
            [ResearchEvidenceValueRef, ResearchEvidence], Any
        ]
        | None = None,
    ) -> None:
        self._semantic_scope = semantic_scope
        self._schema_snapshot = schema_snapshot
        self._evidence_value_resolver = evidence_value_resolver

    def build(
        self,
        query: ResearchSemanticQuery,
        requirement: ResearchAgentRequirement | None = None,
        evidence: Collection[ResearchEvidence] = (),
        *,
        semantic_scope: SemanticAssetScope | None = None,
        schema_snapshot: Any | None = None,
        evidence_value_resolver: Callable[
            [ResearchEvidenceValueRef, ResearchEvidence], Any
        ]
        | None = None,
    ) -> AnalysisExecutionSpec:
        """生成受控 QueryRequirement/CalculationRequirement DAG。"""

        if requirement is not None:
            requirement.validate_query(query, evidence)
            research_scope = requirement.scope
        else:
            query.validate_scope(_research_scope_from_semantic_scope(semantic_scope or self._semantic_scope), evidence)
            research_scope = None
        bound_scope = semantic_scope or self._semantic_scope
        if bound_scope is None:
            raise SemanticQueryBuildError("SEMANTIC_SCOPE_REQUIRED")
        schema = schema_snapshot or self._schema_snapshot or bound_scope.schema_snapshot
        self._active_schema = schema
        asset_map = self._asset_map(bound_scope)
        metric_bindings = {
            ref: self._resolve_asset(ref, "METRIC", asset_map, research_scope)
            for ref in query.metrics
        }
        dimension_bindings = {
            ref: self._resolve_asset(ref, "DIMENSION", asset_map, research_scope)
            for ref in query.dimensions
        }
        filters = self._build_filters(
            query,
            evidence,
            asset_map,
            research_scope,
            evidence_value_resolver=evidence_value_resolver,
        )
        time_bindings = self._time_bindings(query, requirement)
        model_ids = self._model_ids(metric_bindings.values())
        if not model_ids:
            raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
        if query.comparison in {
            ResearchQueryComparison.SHARE,
            ResearchQueryComparison.CONTRIBUTION,
        } and len(query.metrics) != 1:
            # 当前确定性计算契约仅支持单指标，占比或贡献度不能静默忽略其余指标。
            raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
        if (
            query.comparison is ResearchQueryComparison.SHARE
            and len(query.time_ranges) != 1
        ):
            raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
        dimensions_by_model, aliases_by_model, dimension_refs_by_model = (
            self._dimensions_by_model(
                query,
                dimension_bindings,
                asset_map,
                research_scope,
                model_ids,
            )
        )
        query_ids: list[str] = []
        requirements: list[QueryRequirement] = []
        for role in query.time_ranges:
            role_bindings = self._bindings_for_role(role, time_bindings)
            for model_id in model_ids:
                metrics = tuple(
                    self._asset_payload(ref, metric_bindings[ref])
                    for ref in query.metrics
                    if self._model_id(metric_bindings[ref]) == model_id
                )
                if not metrics:
                    continue
                model_dimensions = dimensions_by_model.get(model_id, ())
                model_filters = self._filters_for_model(
                    filters,
                    model_id=model_id,
                    asset_map=asset_map,
                    research_scope=research_scope,
                    model_ids=model_ids,
                )
                time_binding = role_bindings.get(model_id)
                if time_binding is None:
                    # 每个实际查询模型都必须拥有当前时间角色的明确绑定，不能
                    # 用空时间条件或其他模型的时间维度继续执行。
                    raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
                binding_model_id, _ = self._parse_ref(time_binding.dimension_ref)
                if binding_model_id != model_id:
                    raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
                query_id = self._query_id(role, model_id, len(model_ids))
                query_ids.append(query_id)
                requirements.append(
                    QueryRequirement(
                        id=query_id,
                        model_ref=f"MODEL:{model_id}",
                        metrics=metrics,
                        group_by=model_dimensions,
                        filters=tuple(self._filter_payload(item) for item in model_filters),
                        time=self._time_payload(time_binding),
                        order_by=self._order_by_for_model(
                            query,
                            metric_bindings,
                            asset_map,
                            dimension_refs_by_model.get(model_id, {}),
                            model_id,
                        ),
                        limit=query.limit,
                        query_shape=self._query_shape(query),
                        output_aliases=aliases_by_model.get(model_id, {}),
                    )
                )
        if not requirements:
            raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
        if query.analysis == "contribution":
            requirements.extend(
                item.model_copy(
                    update={
                        "id": f"{item.id}:total",
                        "group_by": (),
                        "output_aliases": {},
                        "query_shape": {
                            **item.query_shape,
                            "needs_group_by": False,
                        },
                    }
                )
                for item in tuple(requirements)
            )

        calculations: list[CalculationRequirement] = []
        role_results = self._merge_by_role(
            requirements,
            model_ids=model_ids,
            join_keys=self._join_keys(dimension_refs_by_model),
        )
        if role_results:
            calculations.extend(role_results[0])
            requirements = self._merge_query_requirements(requirements, role_results[1])
        final_id, calculations = self._comparison_calculations(
            query,
            requirements,
            calculations,
            metric_bindings,
            self._join_keys(dimension_refs_by_model),
        )
        if query.analysis == "contribution":
            final_id, calculations = self._contribution_calculations(
                query,
                requirements,
                calculations,
                metric_bindings,
                self._join_keys(dimension_refs_by_model),
                final_id,
                contribution_tolerance=(
                    research_scope.contribution_tolerance
                    if research_scope is not None
                    else None
                ),
            )
        if final_id is None:
            final_id = self._primary_id(requirements, calculations)
        result_contract = ExecutionResultContract(
            primary_requirement_id=final_id,
            supporting_requirement_ids=(),
            ordered_requirement_ids=(final_id,),
            analysis_type=(
                "fixed_attribution"
                if query.analysis == "contribution"
                else "fixed_drilldown"
                if query.analysis == "drilldown"
                else "standard"
            ),
        )
        runtime = {
            "dataset_id": bound_scope.dataset_id,
            "run_id": query.run_id,
            "purpose": query.purpose,
            "hypothesis_ids": list(query.hypothesis_ids),
            "analysis": query.analysis,
            "logical_result_columns": self._logical_result_columns(query),
        }
        if requirement is not None:
            runtime["tenant_scope"] = requirement.scope.tenant_scope
        asset_snapshot = {
            "schema_fingerprint": query.version_snapshot.schema_fingerprint,
            "schema_version": query.version_snapshot.schema_version,
            "contract_version": query.version_snapshot.contract_version,
        }
        if schema is not None:
            asset_snapshot["dataset_schema"] = self._dump_model(schema)
        return AnalysisExecutionSpec(
            query_requirements=tuple(requirements),
            post_calculations=tuple(calculations),
            result_contract=result_contract,
            runtime=runtime,
            asset_snapshot=asset_snapshot,
        )

    @staticmethod
    def _asset_map(scope: SemanticAssetScope) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in scope.allowed_assets:
            asset_type = getattr(item.asset_type, "value", item.asset_type)
            if item.model_id is None:
                # 时间计划可能只携带物理维度而未标注模型；查询引用仍必须
                # 使用带模型 ID 的冻结绑定，因此此类资产不能参与逻辑解析。
                continue
            key = SemanticQueryBuilder._ref_key(
                str(asset_type), item.model_id, item.asset_id
            )
            result[key] = item
        # 严格计划在某些旧检索结果中只保存计划，不重复列出资产引用；
        # 这里仍允许计划中的物理资产由运行时 Scope 校验，不会创建虚假 ID。
        for plan in scope.query_plans or ((scope.query_plan,) if scope.query_plan else ()):
            for metric_binding in plan.metrics:
                key = SemanticQueryBuilder._ref_key(
                    "METRIC", metric_binding.model_id, metric_binding.metric_id
                )
                result.setdefault(key, metric_binding)
            for dimension_binding in plan.dimensions:
                key = SemanticQueryBuilder._ref_key(
                    "DIMENSION",
                    dimension_binding.model_id,
                    dimension_binding.physical_dimension_id,
                )
                result.setdefault(key, dimension_binding)
        return result

    @staticmethod
    def _ref_key(asset_type: str, model_id: int | None, asset_id: int) -> str:
        if model_id is None or model_id <= 0 or asset_id <= 0:
            raise SemanticQueryBuildError("SEMANTIC_ASSET_REFERENCE_INVALID")
        return f"{asset_type.upper()}:{model_id}:{asset_id}"

    @staticmethod
    def _resolve_asset(
        ref: str,
        asset_type: str,
        asset_map: dict[str, Any],
        research_scope: Any | None,
    ) -> Any:
        if not ref.startswith(f"{asset_type}:"):
            raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
        parts = ref.split(":")
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            raise SemanticQueryBuildError("SEMANTIC_ASSET_REFERENCE_INVALID")
        if research_scope is not None:
            allowed = (
                set(research_scope.target_metric_refs)
                | set(research_scope.driver_metric_refs)
                if asset_type == "METRIC"
                else set(research_scope.dimension_refs)
            )
            if ref not in allowed:
                raise SemanticQueryBuildError("SCOPE_DENIED")
        key = SemanticQueryBuilder._ref_key(asset_type, int(parts[1]), int(parts[2]))
        asset = asset_map.get(key)
        if asset is None:
            raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
        return asset

    @staticmethod
    def _asset_payload(ref: str, asset: Any) -> dict[str, Any]:
        model_id, asset_id = SemanticQueryBuilder._parse_ref(ref)
        payload = {
            "ref": ref,
            "asset_id": asset_id,
            "model_id": model_id,
        }
        if hasattr(asset, "logical_dimension_id"):
            payload["logical_dimension_id"] = asset.logical_dimension_id
        return payload

    @staticmethod
    def _parse_ref(ref: str) -> tuple[int, int]:
        parts = ref.split(":")
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            raise SemanticQueryBuildError("SEMANTIC_ASSET_REFERENCE_INVALID")
        return int(parts[1]), int(parts[2])

    @staticmethod
    def _model_id(asset: Any) -> int | None:
        value = getattr(asset, "model_id", None)
        return value if isinstance(value, int) and value > 0 else None

    @staticmethod
    def _model_ids(assets: Iterable[Any]) -> tuple[int, ...]:
        return tuple(dict.fromkeys(item for item in (SemanticQueryBuilder._model_id(asset) for asset in assets) if item is not None))

    def _build_filters(
        self,
        query: ResearchSemanticQuery,
        evidence: Collection[ResearchEvidence],
        asset_map: dict[str, Any],
        research_scope: Any | None,
        *,
        evidence_value_resolver: Callable[
            [ResearchEvidenceValueRef, ResearchEvidence], Any
        ]
        | None = None,
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for item in query.filters:
            self._resolve_asset(item.target_ref, "DIMENSION", asset_map, research_scope)
            values.append(
                {
                    "target_ref": item.target_ref,
                    "asset_id": self._parse_ref(item.target_ref)[1],
                    "model_id": self._parse_ref(item.target_ref)[0],
                    "operator": item.operator,
                    "value": item.value,
                }
            )
        for value_ref in query.evidence_value_filters:
            self._resolve_asset(value_ref.target_ref, "DIMENSION", asset_map, research_scope)
            evidence_item = next(
                (item for item in evidence if item.evidence_id == value_ref.evidence_id),
                None,
            )
            if evidence_item is None:
                raise SemanticQueryBuildError("EVIDENCE_REFERENCE_INVALID")
            resolver = evidence_value_resolver or self._evidence_value_resolver
            if resolver is None:
                # sample_rows 只用于模型上下文，不能成为受信查询筛选值来源。
                raise SemanticQueryBuildError("RESULT_STORE_FAILED")
            value = resolver(value_ref, evidence_item)
            model_id, asset_id = self._parse_ref(value_ref.target_ref)
            values.append(
                {
                    "target_ref": value_ref.target_ref,
                    "asset_id": asset_id,
                    "model_id": model_id,
                    "operator": "=",
                    "value": value,
                }
            )
        return values

    def _dimensions_by_model(
        self,
        query: ResearchSemanticQuery,
        dimension_bindings: dict[str, Any],
        asset_map: dict[str, Any],
        research_scope: Any | None,
        model_ids: tuple[int, ...],
    ) -> tuple[
        dict[int, tuple[dict[str, Any], ...]],
        dict[int, dict[int, str]],
        dict[int, dict[str, str]],
    ]:
        """按已发布跨模型关系投影维度，并为同一逻辑维度分配统一列名。"""

        dimensions_by_model: dict[int, list[dict[str, Any]]] = {
            model_id: [] for model_id in model_ids
        }
        aliases_by_model: dict[int, dict[int, str]] = {
            model_id: {} for model_id in model_ids
        }
        refs_by_model: dict[int, dict[str, str]] = {
            model_id: {} for model_id in model_ids
        }
        for index, requested_ref in enumerate(query.dimensions, start=1):
            alias = f"dimension_{index}"
            for model_id in model_ids:
                actual_ref = self._dimension_ref_for_model(
                    requested_ref,
                    model_id=model_id,
                    research_scope=research_scope,
                )
                if actual_ref in refs_by_model[model_id].values():
                    raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
                asset = dimension_bindings.get(requested_ref)
                if actual_ref != requested_ref:
                    asset = self._resolve_asset(
                        actual_ref,
                        "DIMENSION",
                        asset_map,
                        research_scope,
                    )
                if asset is None or self._model_id(asset) != model_id:
                    raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
                refs_by_model[model_id][requested_ref] = actual_ref
                payload = self._asset_payload(actual_ref, asset)
                dimensions_by_model[model_id].append(payload)
                asset_id = int(payload["asset_id"])
                if asset_id in aliases_by_model[model_id]:
                    raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
                aliases_by_model[model_id][asset_id] = alias
        return (
            {key: tuple(value) for key, value in dimensions_by_model.items()},
            aliases_by_model,
            refs_by_model,
        )

    @staticmethod
    def _dimension_ref_for_model(
        requested_ref: str,
        *,
        model_id: int,
        research_scope: Any | None,
    ) -> str:
        requested_model, _ = SemanticQueryBuilder._parse_ref(requested_ref)
        if requested_model == model_id:
            return requested_ref
        if research_scope is None:
            raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
        candidates: list[str] = []
        for relationship in research_scope.driver_relationships:
            mapping = relationship.dimension_refs_by_model
            source_refs = tuple(
                ref
                for refs in mapping.values()
                for ref in refs
                if ref == requested_ref
            )
            if not source_refs:
                continue
            source_model = str(requested_model)
            source_dimensions = tuple(mapping.get(source_model, ()))
            target_dimensions = tuple(mapping.get(str(model_id), ()))
            if not source_dimensions or not target_dimensions:
                continue
            try:
                source_index = source_dimensions.index(requested_ref)
                candidate = target_dimensions[source_index]
            except (ValueError, IndexError):
                continue
            if candidate not in candidates:
                candidates.append(candidate)
        if len(candidates) != 1:
            raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
        return candidates[0]

    def _filters_for_model(
        self,
        filters: list[dict[str, Any]],
        *,
        model_id: int,
        asset_map: dict[str, Any],
        research_scope: Any | None,
        model_ids: tuple[int, ...],
    ) -> tuple[dict[str, Any], ...]:
        """确保每个模型都保留筛选；无法映射时明确拒绝而不是丢弃。"""

        result: list[dict[str, Any]] = []
        for item in filters:
            target_ref = str(item["target_ref"])
            target_model, _ = self._parse_ref(target_ref)
            if target_model not in model_ids:
                raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
            actual_ref = self._dimension_ref_for_model(
                target_ref,
                model_id=model_id,
                research_scope=research_scope,
            )
            asset = self._resolve_asset(
                actual_ref,
                "DIMENSION",
                asset_map,
                research_scope,
            )
            actual_model, actual_asset_id = self._parse_ref(actual_ref)
            if self._model_id(asset) != model_id or actual_model != model_id:
                raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
            result.append(
                {
                    **item,
                    "target_ref": actual_ref,
                    "asset_id": actual_asset_id,
                    "model_id": model_id,
                }
            )
        return tuple(result)

    @staticmethod
    def _filter_payload(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "asset_id": item["asset_id"],
            "model_id": item["model_id"],
            "operator": item["operator"],
            "value": item["value"],
        }

    @staticmethod
    def _time_bindings(
        query: ResearchSemanticQuery,
        requirement: ResearchAgentRequirement | None,
    ) -> dict[ResearchTimeRole, dict[int, ResearchTimeBinding]]:
        if requirement is None:
            return {}
        result: dict[ResearchTimeRole, dict[int, ResearchTimeBinding]] = {}
        default_by_role = {item.role: item for item in requirement.time_bindings}
        by_model = requirement.time_bindings_by_model
        for role in query.time_ranges:
            bindings: dict[int, ResearchTimeBinding] = {}
            for model_key, items in by_model.items():
                for item in items:
                    if item.role is role:
                        bindings[int(model_key)] = item
            default = default_by_role.get(role)
            if default is not None:
                model_id, _ = SemanticQueryBuilder._parse_ref(default.dimension_ref)
                bindings.setdefault(model_id, default)
            result[role] = bindings
        return result

    @staticmethod
    def _bindings_for_role(
        role: ResearchTimeRole,
        bindings: dict[ResearchTimeRole, dict[int, ResearchTimeBinding]],
    ) -> dict[int, ResearchTimeBinding]:
        return dict(bindings.get(role, {}))

    @staticmethod
    def _time_payload(binding: ResearchTimeBinding | None) -> dict[str, Any] | None:
        if binding is None:
            return None
        _, dimension_id = SemanticQueryBuilder._parse_ref(binding.dimension_ref)
        return {
            "role": binding.role.value,
            "expression": binding.expression,
            "dimension_id": dimension_id,
            "normalized": dict(binding.normalized),
        }

    @staticmethod
    def _query_id(role: ResearchTimeRole, model_id: int, model_count: int) -> str:
        suffix = f":m{model_id}" if model_count > 1 else ""
        return f"{role.value}{suffix}"

    @staticmethod
    def _query_shape(query: ResearchSemanticQuery) -> dict[str, Any]:
        return {
            "shape": query.analysis,
            "select_mode": "aggregate",
            "needs_group_by": bool(query.dimensions),
        }

    @staticmethod
    def _order_by(
        query: ResearchSemanticQuery,
        metrics: dict[str, Any],
        dimensions: dict[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        result: list[dict[str, Any]] = []
        for item in query.order:
            asset = metrics.get(item.ref) or dimensions.get(item.ref)
            if asset is None:
                raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
            _, asset_id = SemanticQueryBuilder._parse_ref(item.ref)
            result.append({"asset_id": asset_id, "direction": item.direction.value})
        return tuple(result)

    def _order_by_for_model(
        self,
        query: ResearchSemanticQuery,
        metric_bindings: dict[str, Any],
        asset_map: dict[str, Any],
        dimension_refs: dict[str, str],
        model_id: int,
    ) -> tuple[dict[str, Any], ...]:
        result: list[dict[str, Any]] = []
        metric_refs = set(metric_bindings)
        for item in query.order:
            actual_ref = item.ref
            if item.ref in metric_refs:
                if self._parse_ref(item.ref)[0] != model_id:
                    raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
            elif item.ref in dimension_refs:
                actual_ref = dimension_refs[item.ref]
            else:
                raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
            asset = metric_bindings.get(actual_ref) or asset_map.get(actual_ref)
            if asset is None or self._model_id(asset) != model_id:
                raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
            _, asset_id = self._parse_ref(actual_ref)
            result.append({"asset_id": asset_id, "direction": item.direction.value})
        return tuple(result)

    @staticmethod
    def _output_aliases(dimensions: tuple[dict[str, Any], ...]) -> dict[int, str]:
        return {
            int(item["asset_id"]): f"dimension_{index}"
            for index, item in enumerate(dimensions, start=1)
        }

    @staticmethod
    def _merge_query_requirements(
        requirements: list[QueryRequirement],
        merges: tuple[QueryRequirement, ...],
    ) -> list[QueryRequirement]:
        return [*requirements, *merges]

    def _merge_by_role(
        self,
        requirements: list[QueryRequirement],
        *,
        model_ids: tuple[int, ...],
        join_keys: tuple[str, ...],
    ) -> tuple[list[CalculationRequirement], tuple[QueryRequirement, ...]] | None:
        if len(model_ids) <= 1:
            return None
        grouped: dict[str, list[QueryRequirement]] = {}
        for item in requirements:
            role = item.id.split(":", 1)[0]
            if item.id.endswith(":total"):
                role = f"{role}_total"
            grouped.setdefault(role, []).append(item)
        calculations: list[CalculationRequirement] = []
        merge_requirements: list[QueryRequirement] = []
        for role, items in grouped.items():
            item_models = {
                int(item.model_ref.split(":", 1)[1])
                for item in items
                if item.model_ref.startswith("MODEL:")
                and item.model_ref.split(":", 1)[1].isdigit()
            }
            if len(items) != len(model_ids) or item_models != set(model_ids):
                raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
            merge_id = f"{role}:merge"
            calculations.append(
                CalculationRequirement(
                    id=merge_id,
                    type=CalculationOperation.MERGE,
                    inputs=tuple(item.id for item in items),
                    # total 查询没有维度列，不能使用明细查询的连接键。
                    join_keys=() if role.endswith("_total") else join_keys,
                )
            )
        return calculations, tuple(merge_requirements)

    @staticmethod
    def _join_keys(dimension_refs_by_model: dict[int, dict[str, str]]) -> tuple[str, ...]:
        """返回跨模型合并使用的逻辑维度列名，而不是物理维度 ID。"""

        if not dimension_refs_by_model:
            return ()
        first = next(iter(dimension_refs_by_model.values()))
        return tuple(f"dimension_{index}" for index in range(1, len(first) + 1))

    @staticmethod
    def _output_aliases_for_refs(dimensions: dict[str, Any]) -> tuple[str, ...]:
        return tuple(f"dimension_{index}" for index, _ in enumerate(dimensions, start=1))

    def _comparison_calculations(
        self,
        query: ResearchSemanticQuery,
        requirements: list[QueryRequirement],
        calculations: list[CalculationRequirement],
        metrics: dict[str, Any],
        join_keys: tuple[str, ...],
    ) -> tuple[str | None, list[CalculationRequirement]]:
        if query.comparison is ResearchQueryComparison.SHARE:
            if not join_keys:
                raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
            source = self._primary_id(requirements, calculations)
            metric = self._metric_field(metrics, query.metrics[0])
            calculations.append(
                CalculationRequirement(
                    id="share",
                    type=CalculationOperation.SHARE,
                    inputs=(source,),
                    value_columns=(metric,),
                    options={"dimensions": list(join_keys)},
                )
            )
            return "share", calculations
        if query.comparison not in {
            ResearchQueryComparison.DIFFERENCE,
            ResearchQueryComparison.GROWTH_RATE,
        }:
            return None, calculations
        current = self._role_id(requirements, calculations, ResearchTimeRole.CURRENT)
        previous = self._role_id(requirements, calculations, ResearchTimeRole.PREVIOUS)
        if current is None or previous is None:
            raise SemanticQueryBuildError(
                "RESEARCH_AGENT_COMPARISON_TIME_ROLES_REQUIRED"
            )
        metric_fields = tuple(self._metric_field(metrics, ref) for ref in query.metrics)
        calc_id = "comparison"
        calculations.append(
            CalculationRequirement(
                id=calc_id,
                type=CalculationOperation(query.comparison.value),
                inputs=(current, previous),
                join_keys=join_keys,
                value_columns=metric_fields,
            )
        )
        return calc_id, calculations

    @staticmethod
    def _role_id(
        requirements: list[QueryRequirement],
        calculations: list[CalculationRequirement],
        role: ResearchTimeRole,
        *,
        total: bool = False,
    ) -> str | None:
        prefixes = (
            (f"{role.value}_total", f"{role.value}:total")
            if total
            else (role.value,)
        )
        for calculation in calculations:
            for prefix in prefixes:
                if calculation.id == prefix or calculation.id.startswith(f"{prefix}:"):
                    return calculation.id
        for requirement in requirements:
            for prefix in prefixes:
                if requirement.id == prefix or requirement.id.startswith(f"{prefix}:"):
                    return requirement.id
        return None

    def _contribution_calculations(
        self,
        query: ResearchSemanticQuery,
        requirements: list[QueryRequirement],
        calculations: list[CalculationRequirement],
        metrics: dict[str, Any],
        join_keys: tuple[str, ...],
        final_id: str | None,
        contribution_tolerance: float | None,
    ) -> tuple[str, list[CalculationRequirement]]:
        if final_id is not None:
            return final_id, calculations
        current = self._role_id(requirements, calculations, ResearchTimeRole.CURRENT)
        previous = self._role_id(requirements, calculations, ResearchTimeRole.PREVIOUS)
        if current is None or previous is None:
            raise SemanticQueryBuildError(
                "RESEARCH_AGENT_COMPARISON_TIME_ROLES_REQUIRED"
            )
        difference_id = "breakdown_difference"
        total_difference_id = "total_difference"
        metric = self._metric_field(metrics, query.metrics[0])
        total_current = self._role_id(
            requirements, calculations, ResearchTimeRole.CURRENT, total=True
        )
        total_previous = self._role_id(
            requirements, calculations, ResearchTimeRole.PREVIOUS, total=True
        )
        if total_current is None or total_previous is None:
            raise SemanticQueryBuildError(
                "RESEARCH_AGENT_CONTRIBUTION_TOTAL_QUERY_REQUIRED"
            )
        if contribution_tolerance is None:
            raise SemanticQueryBuildError("UNSUPPORTED_CAPABILITY")
        calculations.extend(
            [
                CalculationRequirement(
                    id=difference_id,
                    type=CalculationOperation.DIFFERENCE,
                    inputs=(current, previous),
                    join_keys=join_keys,
                    value_columns=(metric,),
                ),
                CalculationRequirement(
                    id=total_difference_id,
                    type=CalculationOperation.DIFFERENCE,
                    inputs=(total_current, total_previous),
                    join_keys=(),
                    value_columns=(metric,),
                ),
                CalculationRequirement(
                    id="contribution",
                    type=CalculationOperation.CONTRIBUTION,
                    inputs=(difference_id, total_difference_id),
                    options={
                        "dimensions": list(join_keys),
                        "difference_column": f"{metric}_difference",
                        "total_difference_column": f"{metric}_difference",
                        "output_column": f"{metric}_contribution",
                        "reconciliation_tolerance": contribution_tolerance,
                    },
                ),
            ]
        )
        return "contribution", calculations

    @staticmethod
    def _primary_id(
        requirements: list[QueryRequirement], calculations: list[CalculationRequirement]
    ) -> str:
        known = {item.id for item in requirements} | {item.id for item in calculations}
        consumed = {input_id for item in calculations for input_id in item.inputs}
        leaves = known - consumed
        if len(leaves) != 1:
            raise SemanticQueryBuildError(
                "ANALYSIS_EXECUTION_PRIMARY_RESULT_NOT_UNIQUE"
            )
        return next(iter(leaves))

    def _metric_field(self, metrics: dict[str, Any], ref: str) -> str:
        schema = getattr(self, "_active_schema", None) or self._schema_snapshot
        metric_id = self._parse_ref(ref)[1]
        for item in getattr(schema, "metrics", ()) if schema is not None else ():
            if getattr(item, "id", None) == metric_id:
                return str(getattr(item, "biz_name", None) or item.id)
        return str(metric_id)

    def _logical_result_columns(
        self,
        query: ResearchSemanticQuery,
    ) -> list[dict[str, str]]:
        """冻结逻辑资产、值角色与最终结果字段的确定性映射。"""

        result = [
            {
                "asset_ref": ref,
                "value_role": "group_key",
                "result_field": f"dimension_{index}",
            }
            for index, ref in enumerate(query.dimensions, start=1)
        ]
        role = {
            ResearchQueryComparison.DIFFERENCE: "difference",
            ResearchQueryComparison.GROWTH_RATE: "growth_rate",
            ResearchQueryComparison.SHARE: "share",
            ResearchQueryComparison.CONTRIBUTION: "contribution",
        }.get(query.comparison, "value")
        suffix = None if role == "value" else role
        for ref in query.metrics:
            field = self._metric_field({}, ref)
            result.append(
                {
                    "asset_ref": ref,
                    "value_role": role,
                    "result_field": f"{field}_{suffix}" if suffix else field,
                }
            )
        return result

    @staticmethod
    def _dump_model(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return value


def _research_scope_from_semantic_scope(scope: SemanticAssetScope | None) -> Any:
    if scope is None:
        raise SemanticQueryBuildError("SEMANTIC_SCOPE_REQUIRED")
    # 没有 ResearchAgentRequirement 时，SemanticAssetScope 仍能完成物理资产边界校验；
    # Research 级别的冻结 WHAT 校验由 Runtime 在进入 Builder 前完成。
    assets = []
    for item in scope.allowed_assets:
        asset_type = getattr(item.asset_type, "value", item.asset_type)
        if item.model_id is None:
            continue
        assets.append(f"{asset_type}:{item.model_id}:{item.asset_id}")

    class _Scope:
        target_metric_refs: tuple[str, ...] = tuple(
            item for item in assets if item.startswith("METRIC:")
        )
        driver_metric_refs: tuple[str, ...] = ()
        dimension_refs: tuple[str, ...] = tuple(
            item for item in assets if item.startswith("DIMENSION:")
        )
        allowed_filter_refs: tuple[str, ...] = dimension_refs
        contribution_metric_refs: tuple[str, ...] = target_metric_refs
        contribution_dimension_refs: tuple[str, ...] = dimension_refs
        hierarchies: tuple[Any, ...] = ()

    return _Scope()


__all__ = ["SemanticQueryBuilder"]
