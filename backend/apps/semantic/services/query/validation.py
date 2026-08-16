"""语义查询计划的确定性验证。"""

from __future__ import annotations

from apps.semantic.models.dto import (
    DatasetSchema,
    SchemaElement,
    SemanticPlanStatus,
    SemanticPlanValidationReport,
    SemanticQueryPlan,
    SemanticValidationCheck,
    SemanticValidationReasonCode,
)
from apps.semantic.services.compilation.time_offset import (
    TimeOffsetError,
    decide_time_offset,
)


class SemanticQueryValidationService:
    """集中验证查询计划的资产、能力、关系、粒度、时间和版本。"""

    def validate(
        self,
        plan: SemanticQueryPlan,
        schema: DatasetSchema,
    ) -> SemanticPlanValidationReport:
        checks: list[SemanticValidationCheck] = []
        checks.extend(self._validate_schema_version(plan, schema))
        checks.extend(self._validate_metrics(plan, schema))
        checks.extend(self._validate_dimensions(plan, schema))
        checks.extend(self._validate_relations(plan, schema))
        checks.extend(self._validate_aggregation(plan, schema))
        checks.extend(self._validate_time(plan, schema))
        checks.extend(self._validate_filters(plan, schema))
        checks.extend(self._validate_expression_extensions(plan, schema))
        reason_codes = tuple(
            dict.fromkeys(
                item.reason_code
                for item in checks
                if item.status == "FAIL" and item.reason_code
            )
        )
        status = self._status(reason_codes)
        return SemanticPlanValidationReport(
            status=status,
            checks=tuple(checks),
            reason_codes=reason_codes,
            evidence={
                "schema_fingerprint": schema.schema_fingerprint,
                "plan_fingerprint": plan.fingerprint,
                "metric_ids": [item.metric_id for item in plan.metrics],
                "logical_dimension_ids": [
                    item.logical_dimension_id for item in plan.dimensions
                ],
            },
        )

    def _validate_schema_version(
        self,
        plan: SemanticQueryPlan,
        schema: DatasetSchema,
    ) -> list[SemanticValidationCheck]:
        checks: list[SemanticValidationCheck] = []
        if plan.dataset_id != schema.data_set.id:
            checks.append(
                _fail(
                    "DATASET_ID",
                    f"dataset:{plan.dataset_id}",
                    "查询计划数据集与运行时 Schema 不一致",
                    SemanticValidationReasonCode.SEMANTIC_ASSET_VERSION_CHANGED,
                )
            )
        if plan.schema_version != schema.schema_version:
            checks.append(
                _fail(
                    "SCHEMA_VERSION",
                    f"dataset:{plan.dataset_id}",
                    "数据集 Schema 版本已变化，必须重新规划",
                    SemanticValidationReasonCode.SEMANTIC_ASSET_VERSION_CHANGED,
                )
            )
        if plan.contract_version != schema.contract_version:
            checks.append(
                _fail(
                    "CONTRACT_VERSION",
                    f"dataset:{plan.dataset_id}",
                    "语义契约版本已变化，必须重新规划",
                    SemanticValidationReasonCode.SEMANTIC_ASSET_VERSION_CHANGED,
                )
            )
        return checks

    def _validate_expression_extensions(
        self,
        plan: SemanticQueryPlan,
        schema: DatasetSchema,
    ) -> list[SemanticValidationCheck]:
        """校验 P1-6 的比率保护、时间偏移和预聚合粒度。"""

        checks: list[SemanticValidationCheck] = []
        metrics = {item.id: item for item in schema.metrics}
        for binding in plan.metrics:
            metric = metrics.get(binding.metric_id)
            if metric is None:
                continue
            params = metric.type_params or {}
            metric_params = params.get("metricDefineByMetricParams") or {}
            refs = metric_params.get("metrics") if isinstance(metric_params, dict) else []
            expression = str(metric_params.get("expr") or "").lower() if isinstance(metric_params, dict) else ""
            if isinstance(refs, list) and len(refs) == 2 and "/" in expression and "nullif" not in expression:
                checks.append(
                    _fail(
                        "METRIC_RATIO_PROTECTION",
                        f"metric:{binding.metric_id}",
                        "比率指标的分母必须具备除零保护",
                        SemanticValidationReasonCode.METRIC_RATIO_UNSAFE,
                    )
                )
        if plan.time_offset:
            try:
                decide_time_offset(
                    method=str(plan.time_offset.get("method") or ""),
                    grain=str(plan.time_offset.get("grain") or plan.time_binding.grain or ""),
                    range_count=int(plan.time_offset.get("range_count") or 1),
                )
            except (TimeOffsetError, TypeError, ValueError):
                checks.append(
                    _fail(
                        "TIME_OFFSET",
                        "query:time_offset",
                        "时间偏移参数不满足固定周期编译约束",
                        SemanticValidationReasonCode.TIME_OFFSET_INVALID,
                    )
                )
        if plan.model_plan.pre_aggregation_required and not plan.model_plan.pre_aggregation_grain:
            checks.append(
                _fail(
                    "PRE_AGGREGATION_GRAIN",
                    "query:model_plan",
                    "预聚合计划必须声明目标粒度",
                    SemanticValidationReasonCode.PRE_AGGREGATION_GRAIN_INVALID,
                )
            )
        return checks

    def _validate_metrics(
        self,
        plan: SemanticQueryPlan,
        schema: DatasetSchema,
    ) -> list[SemanticValidationCheck]:
        metrics = {item.id: item for item in schema.metrics}
        model_contracts = {
            int(item["model_id"]): item
            for item in schema.model_contracts
            if isinstance(item, dict) and item.get("model_id") is not None
        }
        contracts = {
            int(item["metric_id"]): item
            for item in schema.metric_contracts
            if isinstance(item, dict) and item.get("metric_id") is not None
        }
        checks: list[SemanticValidationCheck] = []
        for binding in plan.metrics:
            subject = f"metric:{binding.metric_id}"
            metric = metrics.get(binding.metric_id)
            contract = contracts.get(binding.metric_id)
            if metric is None or contract is None:
                checks.append(
                    _fail(
                        "METRIC_ASSET",
                        subject,
                        "指标不存在或缺少运行时指标契约",
                        SemanticValidationReasonCode.SEMANTIC_CONTRACT_INCOMPLETE,
                    )
                )
                continue
            expected_version = int(contract.get("contract_version") or 0)
            if binding.version != expected_version:
                checks.append(
                    _fail(
                        "METRIC_VERSION",
                        subject,
                        "指标契约版本与计划绑定版本不一致",
                        SemanticValidationReasonCode.SEMANTIC_ASSET_VERSION_CHANGED,
                    )
                )
            if metric.model != binding.model_id:
                checks.append(
                    _fail(
                        "METRIC_MODEL",
                        subject,
                        "计划中的指标基础模型与资产不一致",
                        SemanticValidationReasonCode.SEMANTIC_CONTRACT_INCOMPLETE,
                    )
                )
            model_contract = model_contracts.get(binding.model_id)
            if model_contract is None or model_contract.get("contract_status") != "READY":
                checks.append(
                    _fail(
                        "MODEL_CONTRACT",
                        f"model:{binding.model_id}",
                        "指标基础模型契约未发布",
                        SemanticValidationReasonCode.SEMANTIC_CONTRACT_INCOMPLETE,
                    )
                )
        checks.extend(self._validate_metric_filter_consistency(plan, metrics))
        return checks

    @staticmethod
    def _validate_metric_filter_consistency(
        plan: SemanticQueryPlan,
        metrics: dict[int, SchemaElement],
    ) -> list[SemanticValidationCheck]:
        """多个指标声明了不同过滤口径时，单条 SQL 无法同时满足，必须在计划层报错。"""

        filters_by_metric: dict[int, str] = {}
        for binding in plan.metrics:
            metric = metrics.get(binding.metric_id)
            if metric is None:
                continue
            filter_sql = str(
                (metric.ext_info or {}).get("filter_sql") or ""
            ).strip()
            if filter_sql:
                filters_by_metric[binding.metric_id] = filter_sql
        if len(set(filters_by_metric.values())) <= 1:
            return []
        return [
            SemanticValidationCheck(
                check_type="METRIC_FILTER",
                status="FAIL",
                subject_refs=[
                    f"metric:{metric_id}" for metric_id in sorted(filters_by_metric)
                ],
                reason_code=SemanticValidationReasonCode.METRIC_FILTER_CONFLICT.value,
                message=(
                    "多个指标声明了不同的过滤口径，单条 SQL 无法同时满足；"
                    "请拆分查询或统一指标口径"
                ),
            )
        ]

    def _validate_dimensions(
        self,
        plan: SemanticQueryPlan,
        schema: DatasetSchema,
    ) -> list[SemanticValidationCheck]:
        dimensions = {item.id: item for item in schema.dimensions}
        logical_dimensions = {
            int(item["id"]): item
            for item in schema.logical_dimensions
            if isinstance(item, dict) and item.get("id") is not None
        }
        capabilities = [
            item
            for item in schema.metric_dimension_capabilities
            if isinstance(item, dict)
        ]
        checks: list[SemanticValidationCheck] = []
        metric_ids = [item.metric_id for item in plan.metrics]
        for binding in plan.dimensions:
            subject = f"logical_dimension:{binding.logical_dimension_id}"
            logical = logical_dimensions.get(binding.logical_dimension_id)
            physical = dimensions.get(binding.physical_dimension_id)
            if logical is None or physical is None:
                checks.append(
                    _fail(
                        "DIMENSION_ASSET",
                        subject,
                        "业务维度或物理维度不存在",
                        SemanticValidationReasonCode.SEMANTIC_CONTRACT_INCOMPLETE,
                    )
                )
                continue
            physical_logical_id = physical.ext_info.get("logical_dimension_id")
            if physical_logical_id is not None and int(physical_logical_id) != binding.logical_dimension_id:
                checks.append(
                    _fail(
                        "DIMENSION_IDENTITY",
                        subject,
                        "物理维度绑定的业务身份与计划不一致",
                        SemanticValidationReasonCode.LOGICAL_DIMENSION_AMBIGUOUS,
                    )
                )
            if physical.model != binding.model_id:
                checks.append(
                    _fail(
                        "DIMENSION_MODEL",
                        subject,
                        "物理维度所属模型与计划不一致",
                        SemanticValidationReasonCode.RELATION_PATH_INVALID,
                    )
                )
            matching_physical = [
                item
                for item in dimensions.values()
                if item.model == binding.model_id
                and int(item.ext_info.get("logical_dimension_id") or -1)
                == binding.logical_dimension_id
            ]
            if len(matching_physical) > 1:
                checks.append(
                    _fail(
                        "DIMENSION_INSTANCE",
                        subject,
                        "同一模型存在多个当前有效的物理维度实例",
                        SemanticValidationReasonCode.LOGICAL_DIMENSION_AMBIGUOUS,
                    )
                )
            for metric_id in metric_ids:
                metric_capabilities = [
                    item
                    for item in capabilities
                    if item.get("metric_id") == metric_id
                    and item.get("logical_dimension_id")
                    == binding.logical_dimension_id
                ]
                if not metric_capabilities:
                    checks.append(
                        _fail(
                            "METRIC_DIMENSION_CAPABILITY",
                            f"metric:{metric_id}/logical_dimension:{binding.logical_dimension_id}",
                            "指标没有声明使用该业务维度的能力",
                            SemanticValidationReasonCode.DIMENSION_NOT_COMPATIBLE_WITH_METRIC,
                        )
                    )
                elif not any(
                    int(item.get("version") or 0) == binding.version
                    for item in metric_capabilities
                ):
                    checks.append(
                        _fail(
                            "CAPABILITY_VERSION",
                            f"metric:{metric_id}/logical_dimension:{binding.logical_dimension_id}",
                            "指标维度能力版本与计划绑定版本不一致",
                            SemanticValidationReasonCode.SEMANTIC_ASSET_VERSION_CHANGED,
                        )
                    )
        return checks

    def _validate_relations(
        self,
        plan: SemanticQueryPlan,
        schema: DatasetSchema,
    ) -> list[SemanticValidationCheck]:
        relations = {
            int(item["relation_id"]): item
            for item in schema.relation_contracts
            if isinstance(item, dict) and item.get("relation_id") is not None
        }
        checks: list[SemanticValidationCheck] = []
        current_model = plan.model_plan.base_model_id
        for relation_id in plan.model_plan.relation_path:
            relation = relations.get(relation_id)
            if relation is None:
                checks.append(
                    _fail(
                        "RELATION_REFERENCE",
                        f"relation:{relation_id}",
                        "计划引用的关系契约不存在",
                        SemanticValidationReasonCode.RELATION_PATH_INVALID,
                    )
                )
                continue
            left = int(relation.get("left_model_id"))
            right = int(relation.get("right_model_id"))
            if current_model == left:
                current_model = right
            elif current_model == right:
                current_model = left
            else:
                checks.append(
                    _fail(
                        "RELATION_CONTINUITY",
                        f"relation:{relation_id}",
                        "关系路径不是连续路径",
                        SemanticValidationReasonCode.RELATION_PATH_INVALID,
                    )
                )
            if relation.get("contract_status") != "READY":
                checks.append(
                    _fail(
                        "RELATION_STATUS",
                        f"relation:{relation_id}",
                        "关系契约未发布",
                        SemanticValidationReasonCode.RELATION_PATH_INVALID,
                    )
                )
            if relation.get("aggregation_safety") == "FORBIDDEN":
                checks.append(
                    _fail(
                        "RELATION_SAFETY",
                        f"relation:{relation_id}",
                        "关系禁止承载指标传播",
                        SemanticValidationReasonCode.JOIN_CAUSES_METRIC_DUPLICATION,
                    )
                )
        return checks

    def _validate_aggregation(
        self,
        plan: SemanticQueryPlan,
        schema: DatasetSchema,
    ) -> list[SemanticValidationCheck]:
        checks: list[SemanticValidationCheck] = []
        result_grains = {
            tuple(item.result_grain)
            for item in plan.metrics
            if item.result_grain
        }
        if len(result_grains) > 1:
            checks.append(
                _fail(
                    "MULTI_METRIC_GRAIN",
                    "query:metrics",
                    "多指标结果粒度不能直接对齐",
                    SemanticValidationReasonCode.MULTI_METRIC_GRAIN_MISMATCH,
                )
            )
        for binding in plan.dimensions:
            if binding.aggregation_safety == "FORBIDDEN":
                checks.append(
                    _fail(
                        "DIMENSION_AGGREGATION_SAFETY",
                        f"logical_dimension:{binding.logical_dimension_id}",
                        "该业务维度禁止用于当前指标聚合",
                        SemanticValidationReasonCode.JOIN_CAUSES_METRIC_DUPLICATION,
                    )
                )
            if binding.aggregation_safety == "PRE_AGGREGATE_REQUIRED" and not plan.model_plan.pre_aggregation_required:
                checks.append(
                    _fail(
                        "PRE_AGGREGATION",
                        f"logical_dimension:{binding.logical_dimension_id}",
                        "该维度需要预聚合但计划未包含预聚合步骤",
                        SemanticValidationReasonCode.JOIN_CAUSES_METRIC_DUPLICATION,
                    )
                )
        return checks

    def _validate_time(
        self,
        plan: SemanticQueryPlan,
        schema: DatasetSchema,
    ) -> list[SemanticValidationCheck]:
        checks: list[SemanticValidationCheck] = []
        time = plan.time_binding
        if time.semantics == "NONE":
            if time.time_range is not None:
                checks.append(
                    _fail(
                        "TIME_SEMANTICS",
                        "query:time",
                        "无时间语义的指标不能接收时间范围",
                        SemanticValidationReasonCode.TIME_SEMANTICS_INCOMPATIBLE,
                    )
                )
            return checks
        if time.dimension_id is None:
            checks.append(
                _fail(
                    "TIME_DIMENSION",
                    "query:time",
                    "时间指标必须绑定时间维度",
                    SemanticValidationReasonCode.TIME_SEMANTICS_INCOMPATIBLE,
                )
            )
        if time.semantics in {"SNAPSHOT", "PERIODIC_SNAPSHOT"} and not time.snapshot_aggregation:
            checks.append(
                _fail(
                    "SNAPSHOT_AGGREGATION",
                    "query:time",
                    "快照指标缺少时间聚合策略",
                    SemanticValidationReasonCode.METRIC_NOT_ADDITIVE_OVER_TIME,
                )
            )
        return checks

    def _validate_filters(
        self,
        plan: SemanticQueryPlan,
        schema: DatasetSchema,
    ) -> list[SemanticValidationCheck]:
        dimension_ids = {item.id for item in schema.dimensions}
        return [
            _fail(
                "FILTER_DIMENSION",
                f"dimension:{item.physical_dimension_id}",
                "筛选条件引用的物理维度不存在",
                SemanticValidationReasonCode.SEMANTIC_CONTRACT_INCOMPLETE,
            )
            for item in plan.filters
            if item.physical_dimension_id not in dimension_ids
        ]

    @staticmethod
    def _status(reason_codes: tuple[str, ...]) -> SemanticPlanStatus:
        if not reason_codes:
            return SemanticPlanStatus.PROVEN
        if SemanticValidationReasonCode.LOGICAL_DIMENSION_AMBIGUOUS in reason_codes:
            return SemanticPlanStatus.CLARIFICATION_REQUIRED
        if any(
            code
            in {
                SemanticValidationReasonCode.SEMANTIC_ASSET_VERSION_CHANGED,
                SemanticValidationReasonCode.SEMANTIC_CONTRACT_INCOMPLETE,
            }
            for code in reason_codes
        ):
            return SemanticPlanStatus.INVALID_CONTRACT
        return SemanticPlanStatus.INFEASIBLE


def _fail(
    check_type: str,
    subject: str,
    message: str,
    reason_code: SemanticValidationReasonCode,
) -> SemanticValidationCheck:
    return SemanticValidationCheck(
        check_type=check_type,
        status="FAIL",
        subject_refs=[subject],
        reason_code=reason_code.value,
        message=message,
    )
