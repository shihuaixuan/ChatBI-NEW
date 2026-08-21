"""完整语义契约的集中式发布校验规则。"""

from collections.abc import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apps.semantic.models.dto.semantic_validation import (
    SemanticValidationCheck,
)
from apps.semantic.models.orm import (
    BusinessEntity,
    DimensionHierarchy,
    DimensionHierarchyLevel,
    LogicalDimension,
    MetricDimensionCapability,
    MetricRelationship,
    MetricRelationshipDimension,
    SemanticDataset,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelRelation,
)
from apps.semantic.services.relation_path import relation_path_error


def validate_semantic_contracts(
    models: Iterable[SemanticModel],
    metrics: Iterable[SemanticMetric],
    dimensions: Iterable[SemanticDimension],
    relations: Iterable[SemanticModelRelation],
    entities: Iterable[BusinessEntity],
    logical_dimensions: Iterable[LogicalDimension],
    capabilities: Iterable[MetricDimensionCapability],
    model_fields: Iterable[SemanticModelField] = (),
    hierarchies: Iterable[DimensionHierarchy] = (),
    hierarchy_levels: Iterable[DimensionHierarchyLevel] = (),
    metric_relationships: Iterable[MetricRelationship] = (),
    relationship_dimensions: Iterable[MetricRelationshipDimension] = (),
    dataset: SemanticDataset | None = None,
) -> list[SemanticValidationCheck]:
    """按统一规则返回全部检查项，不在此处修改持久化对象。"""

    model_list = list(models)
    metric_list = list(metrics)
    dimension_list = list(dimensions)
    relation_list = list(relations)
    entity_list = list(entities)
    logical_dimension_list = list(logical_dimensions)
    capability_list = list(capabilities)
    model_field_list = list(model_fields)
    hierarchy_list = list(hierarchies)
    hierarchy_level_list = list(hierarchy_levels)
    metric_relationship_list = list(metric_relationships)
    relationship_dimension_list = list(relationship_dimensions)
    checks: list[SemanticValidationCheck] = []
    if dataset is not None:
        checks.extend(_validate_dataset_calendar(dataset))
    checks.extend(_validate_models(model_list, model_field_list))
    checks.extend(
        _validate_metrics(
            metric_list,
            model_list,
            dimension_list,
            model_field_list,
        )
    )
    checks.extend(_validate_metric_formula_cycles(metric_list))
    checks.extend(
        _validate_dimensions(
            dimension_list,
            logical_dimension_list,
            entity_list,
            model_list,
        )
    )
    checks.extend(_validate_relations(relation_list, model_list, model_field_list))
    checks.extend(
        _validate_capabilities(
            capability_list,
            metric_list,
            logical_dimension_list,
            relation_list,
            model_list,
            dimension_list,
        )
    )
    checks.extend(
        _validate_dimension_hierarchies(
            hierarchy_list,
            hierarchy_level_list,
            logical_dimension_list,
        )
    )
    checks.extend(
        _validate_metric_relationships(
            metric_relationship_list,
            relationship_dimension_list,
            metric_list,
            logical_dimension_list,
            capability_list,
            model_list,
            relation_list,
        )
    )
    return checks


def _validate_dataset_calendar(
    dataset: SemanticDataset,
) -> list[SemanticValidationCheck]:
    """校验数据集业务日历的最小执行契约。"""

    subject = f"dataset:{dataset.id}"
    checks: list[SemanticValidationCheck] = []
    try:
        ZoneInfo(dataset.default_timezone)
    except (TypeError, ZoneInfoNotFoundError):
        checks.append(
            _check(
                "DATASET_TIMEZONE",
                subject,
                "数据集默认时区不存在",
                "TIME_SEMANTICS_INCOMPATIBLE",
            )
        )
    if dataset.calendar_type not in {"NATURAL", "FISCAL", "BUSINESS"}:
        checks.append(
            _check(
                "DATASET_CALENDAR_TYPE",
                subject,
                "数据集业务日历类型无效",
                "TIME_SEMANTICS_INCOMPATIBLE",
            )
        )
    if (
        not isinstance(dataset.week_start_day, int)
        or not 1 <= dataset.week_start_day <= 7
    ):
        checks.append(
            _check(
                "DATASET_WEEK_START",
                subject,
                "数据集周起始日必须在 1 到 7 之间",
                "TIME_SEMANTICS_INCOMPATIBLE",
            )
        )
    if (
        not isinstance(dataset.fiscal_year_start_month, int)
        or not 1 <= dataset.fiscal_year_start_month <= 12
    ):
        checks.append(
            _check(
                "DATASET_FISCAL_YEAR",
                subject,
                "数据集财年起始月份必须在 1 到 12 之间",
                "TIME_SEMANTICS_INCOMPATIBLE",
            )
        )
    if dataset.calendar_type == "BUSINESS" and not dataset.holiday_calendar_key:
        checks.append(
            _check(
                "DATASET_HOLIDAY_CALENDAR",
                subject,
                "营业日日历必须声明节假日日历标识",
                "TIME_SEMANTICS_INCOMPATIBLE",
            )
        )
    return checks


def _validate_dimension_hierarchies(
    hierarchies: list[DimensionHierarchy],
    levels: list[DimensionHierarchyLevel],
    logical_dimensions: list[LogicalDimension],
) -> list[SemanticValidationCheck]:
    """校验层级节点顺序、唯一性、主题域和认证状态。"""

    logical_by_id = {item.id: item for item in logical_dimensions}
    levels_by_hierarchy: dict[int, list[DimensionHierarchyLevel]] = {}
    for level in levels:
        levels_by_hierarchy.setdefault(level.hierarchy_id, []).append(level)
    checks: list[SemanticValidationCheck] = []
    for hierarchy in hierarchies:
        subject = f"dimension_hierarchy:{hierarchy.id}"
        hierarchy_levels = sorted(
            levels_by_hierarchy.get(hierarchy.id or 0, []),
            key=lambda item: item.level_order,
        )
        if hierarchy.hierarchy_type != "FIXED_LEVEL":
            checks.append(
                _check(
                    "HIERARCHY_TYPE",
                    subject,
                    "维度层级类型无效",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if hierarchy.contract_status not in {"DRAFT", "CERTIFIED"}:
            checks.append(
                _check(
                    "HIERARCHY_STATUS",
                    subject,
                    "维度层级认证状态无效",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if len(hierarchy_levels) < 2:
            checks.append(
                _check(
                    "HIERARCHY_LEVEL_COUNT",
                    subject,
                    "维度层级至少需要两个节点",
                    "SEMANTIC_HIERARCHY_INVALID",
                )
            )
            continue
        orders = [item.level_order for item in hierarchy_levels]
        if orders != list(range(1, len(orders) + 1)):
            checks.append(
                _check(
                    "HIERARCHY_LEVEL_ORDER",
                    subject,
                    "维度层级节点必须从 1 开始连续排序",
                    "SEMANTIC_HIERARCHY_INVALID",
                )
            )
        seen: set[int] = set()
        domain_ids: set[int] = set()
        for level in hierarchy_levels:
            logical = logical_by_id.get(level.logical_dimension_id)
            if logical is None:
                checks.append(
                    _check(
                        "HIERARCHY_DIMENSION_REFERENCE",
                        subject,
                        "维度层级引用的逻辑维度不存在",
                        "SEMANTIC_HIERARCHY_INVALID",
                    )
                )
                continue
            if logical.id in seen:
                checks.append(
                    _check(
                        "HIERARCHY_DIMENSION_DUPLICATED",
                        subject,
                        "维度层级不能重复引用逻辑维度",
                        "SEMANTIC_HIERARCHY_INVALID",
                    )
                )
            seen.add(logical.id or 0)
            domain_ids.add(logical.domain_id)
        if len(domain_ids) > 1 or (
            domain_ids and hierarchy.domain_id not in domain_ids
        ):
            checks.append(
                _check(
                    "HIERARCHY_DOMAIN",
                    subject,
                    "层级及其逻辑维度必须属于同一主题域",
                    "SEMANTIC_CONTRACT_DOMAIN_MISMATCH",
                )
            )
        if hierarchy.contract_status == "CERTIFIED" and any(
            logical_by_id.get(level.logical_dimension_id) is None
            for level in hierarchy_levels
        ):
            checks.append(
                _check(
                    "HIERARCHY_CERTIFICATION",
                    subject,
                    "不完整的维度层级不能认证",
                    "SEMANTIC_HIERARCHY_INVALID",
                )
            )
    return checks


def _validate_metric_relationships(
    relationships: list[MetricRelationship],
    relationship_dimensions: list[MetricRelationshipDimension],
    metrics: list[SemanticMetric],
    logical_dimensions: list[LogicalDimension],
    capabilities: list[MetricDimensionCapability],
    models: list[SemanticModel],
    relations: list[SemanticModelRelation],
) -> list[SemanticValidationCheck]:
    """校验指标关系引用、同模型限制和共同分析维度能力。"""

    metric_by_id = {item.id: item for item in metrics}
    logical_by_id = {item.id: item for item in logical_dimensions}
    model_by_id = {item.id: item for item in models}
    relation_by_id = {
        item.id: item for item in relations if item.id is not None
    }
    dimensions_by_relationship: dict[int, list[int]] = {}
    for item in relationship_dimensions:
        dimensions_by_relationship.setdefault(item.relationship_id, []).append(
            item.logical_dimension_id
        )
    capability_keys = {
        (item.metric_id, item.logical_dimension_id)
        for item in capabilities
        if "GROUP_BY" in {str(usage).upper() for usage in item.usages or []}
        and item.aggregation_safety != "FORBIDDEN"
    }
    checks: list[SemanticValidationCheck] = []
    for relationship in relationships:
        subject = f"metric_relationship:{relationship.id}"
        target = metric_by_id.get(relationship.target_metric_id)
        driver = metric_by_id.get(relationship.driver_metric_id)
        if target is None or driver is None:
            checks.append(
                _check(
                    "METRIC_RELATIONSHIP_REFERENCE",
                    subject,
                    "指标关系引用的指标不存在",
                    "SEMANTIC_METRIC_RELATIONSHIP_INVALID",
                )
            )
            continue
        if target.model_id != driver.model_id:
            if relationship.relationship_type not in {
                "CERTIFIED_DRIVER",
                "GOVERNED_ANALYSIS_RELATION",
            }:
                checks.append(
                    _check(
                        "METRIC_RELATIONSHIP_MODEL",
                        subject,
                        "跨模型关系只能用于已治理驱动分析",
                        "SEMANTIC_METRIC_RELATIONSHIP_CROSS_MODEL",
                    )
                )
            path_error = relation_path_error(
                tuple(relationship.relation_path or []),
                target.model_id,
                driver.model_id,
                relation_by_id,
                # 发布前允许待发布关系，发布事务会统一转换为 READY。
                allowed_contract_statuses={"DRAFT", "CERTIFIED", "READY"},
            )
            if path_error is not None:
                path_messages = {
                    "EMPTY": "跨模型驱动关系必须声明关系路径",
                    "REFERENCE": "指标关系路径包含不存在的模型关系",
                    "STATUS": "指标关系路径包含未通过契约校验的模型关系",
                    "AGGREGATION_SAFETY": "指标关系路径包含禁止承载指标的模型关系",
                    "PROPAGATION": "指标关系路径的指标传播方向不允许当前逐跳方向",
                    "CONTINUITY": "指标关系路径不连续",
                    "TARGET": "指标关系路径未到达驱动指标模型",
                }
                checks.append(
                    _check(
                        "METRIC_RELATIONSHIP_PATH",
                        subject,
                        path_messages[path_error],
                        "RELATION_PATH_INVALID",
                    )
                )
        if target.model_id not in model_by_id:
            checks.append(
                _check(
                    "METRIC_RELATIONSHIP_MODEL_REFERENCE",
                    subject,
                    "指标关系引用的模型不存在",
                    "SEMANTIC_METRIC_RELATIONSHIP_INVALID",
                )
            )
        if relationship.relationship_type not in {
            "FORMULA_COMPONENT",
            "CERTIFIED_DRIVER",
            "GOVERNED_ANALYSIS_RELATION",
        }:
            checks.append(
                _check(
                    "METRIC_RELATIONSHIP_TYPE",
                    subject,
                    "指标关系类型无效",
                    "SEMANTIC_METRIC_RELATIONSHIP_INVALID",
                )
            )
        if relationship.relationship_type == "FORMULA_COMPONENT":
            formula_components = (target.formula_definition or {}).get("components")
            formula_driver_ids = {
                item.get("metric_id")
                for item in formula_components or []
                if isinstance(item, dict)
            }
            if relationship.driver_metric_id not in formula_driver_ids:
                checks.append(
                    _check(
                        "METRIC_FORMULA_RELATIONSHIP",
                        subject,
                        "公式组成关系必须由目标指标的结构化公式确定",
                        "METRIC_FORMULA_RELATIONSHIP_MISMATCH",
                    )
                )
        if relationship.validation_method not in {
            "SAME_DIRECTION",
            "OPPOSITE_DIRECTION",
            "FORMULA_RECONCILIATION",
        }:
            checks.append(
                _check(
                    "METRIC_RELATIONSHIP_VALIDATION",
                    subject,
                    "指标关系验证方式无效",
                    "SEMANTIC_METRIC_RELATIONSHIP_INVALID",
                )
            )
        if relationship.expected_direction not in {"POSITIVE", "NEGATIVE", "UNKNOWN"}:
            checks.append(
                _check(
                    "METRIC_RELATIONSHIP_DIRECTION",
                    subject,
                    "指标关系方向无效",
                    "SEMANTIC_METRIC_RELATIONSHIP_INVALID",
                )
            )
        if (
            (
                relationship.validation_method == "FORMULA_RECONCILIATION"
                and relationship.expected_direction != "UNKNOWN"
            )
            or (
                relationship.validation_method == "SAME_DIRECTION"
                and relationship.expected_direction == "NEGATIVE"
            )
            or (
                relationship.validation_method == "OPPOSITE_DIRECTION"
                and relationship.expected_direction == "POSITIVE"
            )
        ):
            checks.append(
                _check(
                    "METRIC_RELATIONSHIP_DIRECTION",
                    subject,
                    "指标关系验证方式和预期方向冲突",
                    "SEMANTIC_METRIC_RELATIONSHIP_DIRECTION_CONFLICT",
                )
            )
        if not relationship.supported_time_roles:
            checks.append(
                _check(
                    "METRIC_RELATIONSHIP_TIME_ROLE",
                    subject,
                    "指标关系必须声明支持的时间角色",
                    "SEMANTIC_METRIC_RELATIONSHIP_INVALID",
                )
            )
        if (
            target.time_alignment_policy != "NONE"
            and driver.time_alignment_policy != "NONE"
            and target.time_alignment_policy != driver.time_alignment_policy
        ):
            checks.append(
                _check(
                    "METRIC_RELATIONSHIP_TIME_ALIGNMENT",
                    subject,
                    "指标关系双方的时间对齐策略不兼容",
                    "TIME_SEMANTICS_INCOMPATIBLE",
                )
            )
        target_grains = set(target.comparison_grains or [])
        driver_grains = set(driver.comparison_grains or [])
        if target_grains and driver_grains and not target_grains & driver_grains:
            checks.append(
                _check(
                    "METRIC_RELATIONSHIP_TIME_GRAIN",
                    subject,
                    "指标关系双方没有共同的可比较时间粒度",
                    "TIME_SEMANTICS_INCOMPATIBLE",
                )
            )
        for logical_id in dimensions_by_relationship.get(relationship.id or 0, []):
            logical = logical_by_id.get(logical_id)
            if logical is None:
                checks.append(
                    _check(
                        "METRIC_RELATIONSHIP_DIMENSION",
                        subject,
                        "指标关系引用的逻辑维度不存在",
                        "SEMANTIC_METRIC_RELATIONSHIP_INVALID",
                    )
                )
                continue
            if logical.domain_id != relationship.domain_id:
                checks.append(
                    _check(
                        "METRIC_RELATIONSHIP_DOMAIN",
                        subject,
                        "指标关系与共同分析维度必须属于同一主题域",
                        "SEMANTIC_CONTRACT_DOMAIN_MISMATCH",
                    )
                )
            if (target.id, logical_id) not in capability_keys or (
                driver.id,
                logical_id,
            ) not in capability_keys:
                checks.append(
                    _check(
                        "METRIC_RELATIONSHIP_DIMENSION_CAPABILITY",
                        subject,
                        "目标指标和驱动指标必须共同支持每个分析维度",
                        "DIMENSION_NOT_COMPATIBLE_WITH_METRIC",
                    )
                )
        if relationship.contract_status not in {"DRAFT", "CERTIFIED"}:
            checks.append(
                _check(
                    "METRIC_RELATIONSHIP_STATUS",
                    subject,
                    "指标关系认证状态无效",
                    "SEMANTIC_METRIC_RELATIONSHIP_INVALID",
                )
            )
    return checks


def _check(
    check_type: str,
    subject: str,
    message: str,
    reason_code: str | None = None,
) -> SemanticValidationCheck:
    return SemanticValidationCheck(
        check_type=check_type,
        status="FAIL" if reason_code else "PASS",
        subject_refs=[subject],
        reason_code=reason_code,
        message=message,
    )


def _validate_models(
    models: list[SemanticModel],
    model_fields: list[SemanticModelField],
) -> list[SemanticValidationCheck]:
    field_names_by_model: dict[int | None, set[str]] = {}
    for field in model_fields:
        field_names_by_model.setdefault(field.model_id, set()).update(
            {field.field_name, field.biz_name}
        )
    checks: list[SemanticValidationCheck] = []
    for model in models:
        subject = f"model:{model.id}"
        if model.model_kind not in {"ENTITY", "FACT", "DETAIL", "SNAPSHOT", "BRIDGE"}:
            checks.append(
                _check(
                    "MODEL_KIND",
                    subject,
                    "模型必须声明有效的模型类型",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if model.model_kind in {"FACT", "DETAIL", "SNAPSHOT"} and not model.model_grain:
            checks.append(
                _check(
                    "MODEL_GRAIN",
                    subject,
                    "事实、明细和快照模型必须声明粒度",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if model.model_kind == "ENTITY" and not model.primary_key:
            checks.append(
                _check(
                    "MODEL_PRIMARY_KEY",
                    subject,
                    "实体模型必须声明主键",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if model.model_kind == "SNAPSHOT" and not model.snapshot_time_field:
            checks.append(
                _check(
                    "MODEL_SNAPSHOT_TIME",
                    subject,
                    "快照模型必须声明快照时间字段",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if not model.row_description:
            checks.append(
                _check(
                    "MODEL_ROW_DESCRIPTION",
                    subject,
                    "模型必须声明一行数据的业务含义",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        known_fields = field_names_by_model.get(model.id, set())
        if known_fields:
            # 存量数据库可能把 JSON 数组保存为 NULL；校验时按空集合处理并继续报告契约缺失。
            primary_key = model.primary_key or ()
            model_grain = model.model_grain or ()
            referenced_fields = [
                *primary_key,
                *model_grain,
                model.default_time_field,
                model.event_time_field,
                model.snapshot_time_field,
            ]
            if any(
                field_name and field_name not in known_fields
                for field_name in referenced_fields
            ):
                checks.append(
                    _check(
                        "MODEL_FIELD_REFERENCE",
                        subject,
                        "模型主键、粒度或时间字段引用不存在",
                        "SEMANTIC_CONTRACT_INCOMPLETE",
                    )
                )
    return checks


def _validate_metrics(
    metrics: list[SemanticMetric],
    models: list[SemanticModel],
    dimensions: list[SemanticDimension],
    model_fields: list[SemanticModelField],
) -> list[SemanticValidationCheck]:
    model_by_id = {model.id: model for model in models}
    dimension_ids = {dimension.id for dimension in dimensions}
    field_names_by_model: dict[int | None, set[str]] = {}
    for field in model_fields:
        field_names_by_model.setdefault(field.model_id, set()).update(
            {field.field_name, field.biz_name}
        )
    checks: list[SemanticValidationCheck] = []
    for metric in metrics:
        subject = f"metric:{metric.id}"
        if metric.model_id not in model_by_id:
            checks.append(
                _check(
                    "METRIC_MODEL",
                    subject,
                    "指标基础模型不存在",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if not metric.result_grain:
            checks.append(
                _check(
                    "METRIC_GRAIN",
                    subject,
                    "指标必须声明结果粒度",
                    "METRIC_GRAIN_INCOMPATIBLE",
                )
            )
        if not metric.additivity:
            checks.append(
                _check(
                    "METRIC_ADDITIVITY",
                    subject,
                    "指标必须声明可加性",
                    "METRIC_NOT_ADDITIVE",
                )
            )
        if not metric.description:
            checks.append(
                _check(
                    "METRIC_DESCRIPTION",
                    subject,
                    "指标必须声明业务口径",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if metric.default_agg not in {
            "SUM",
            "COUNT",
            "COUNT_DISTINCT",
            "AVG",
            "MIN",
            "MAX",
            "CUSTOM",
        }:
            checks.append(
                _check(
                    "METRIC_DEFAULT_AGG",
                    subject,
                    "指标必须声明有效的默认聚合方式",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if metric.additivity not in {"FULL", "SEMI", "NON_ADDITIVE"}:
            checks.append(
                _check(
                    "METRIC_ADDITIVITY_VALUE",
                    subject,
                    "指标可加性取值无效",
                    "METRIC_NOT_ADDITIVE",
                )
            )
        if metric.time_semantics not in {
            "EVENT",
            "SNAPSHOT",
            "PERIODIC_SNAPSHOT",
            "NONE",
        }:
            checks.append(
                _check(
                    "METRIC_TIME_SEMANTICS",
                    subject,
                    "指标必须声明有效的时间语义",
                    "TIME_SEMANTICS_INCOMPATIBLE",
                )
            )
        if any(
            grain not in {"day", "week", "month", "quarter", "year"}
            for grain in metric.comparison_grains or []
        ):
            checks.append(
                _check(
                    "METRIC_COMPARISON_GRAIN",
                    subject,
                    "指标允许比较的时间粒度无效",
                    "TIME_SEMANTICS_INCOMPATIBLE",
                )
            )
        if metric.time_alignment_policy not in {"SAME_TIME", "AS_OF", "NONE"}:
            checks.append(
                _check(
                    "METRIC_TIME_ALIGNMENT",
                    subject,
                    "指标时间对齐策略无效",
                    "TIME_SEMANTICS_INCOMPATIBLE",
                )
            )
        if metric.time_semantics == "NONE" and metric.time_alignment_policy != "NONE":
            checks.append(
                _check(
                    "METRIC_TIME_ALIGNMENT_SEMANTICS",
                    subject,
                    "无时间语义的指标不能声明时间对齐策略",
                    "TIME_SEMANTICS_INCOMPATIBLE",
                )
            )
        if metric.default_agg == "COUNT_DISTINCT" and not metric.distinct_keys:
            checks.append(
                _check(
                    "METRIC_DISTINCT_KEYS",
                    subject,
                    "去重指标必须声明去重键",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if (
            metric.time_semantics
            and metric.time_semantics != "NONE"
            and metric.default_time_dimension_id is None
        ):
            checks.append(
                _check(
                    "METRIC_TIME_DIMENSION",
                    subject,
                    "时间指标必须声明默认时间维度",
                    "TIME_SEMANTICS_INCOMPATIBLE",
                )
            )
        if (
            metric.default_time_dimension_id is not None
            and metric.default_time_dimension_id not in dimension_ids
        ):
            checks.append(
                _check(
                    "METRIC_TIME_DIMENSION_REFERENCE",
                    subject,
                    "指标默认时间维度不存在",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        elif metric.default_time_dimension_id is not None:
            time_dimension = next(
                item
                for item in dimensions
                if item.id == metric.default_time_dimension_id
            )
            if time_dimension.model_id != metric.model_id:
                checks.append(
                    _check(
                        "METRIC_TIME_DIMENSION_MODEL",
                        subject,
                        "指标默认时间维度不属于基础模型",
                        "TIME_SEMANTICS_INCOMPATIBLE",
                    )
                )
        if (
            metric.time_semantics in {"SNAPSHOT", "PERIODIC_SNAPSHOT"}
            and not metric.snapshot_aggregation
        ):
            checks.append(
                _check(
                    "METRIC_SNAPSHOT_AGGREGATION",
                    subject,
                    "快照指标必须声明快照聚合策略",
                    "METRIC_NOT_ADDITIVE_OVER_TIME",
                )
            )
        checks.extend(_validate_metric_formula(metric, metrics, subject))
        known_fields = field_names_by_model.get(metric.model_id, set())
        if known_fields and any(
            field_name not in known_fields
            for field_name in [*(metric.fields or ()), *(metric.distinct_keys or ())]
        ):
            checks.append(
                _check(
                    "METRIC_FIELD_REFERENCE",
                    subject,
                    "指标字段或去重键引用不存在",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
    return checks


def _validate_metric_formula(
    metric: SemanticMetric,
    metrics: list[SemanticMetric],
    subject: str,
) -> list[SemanticValidationCheck]:
    """校验结构化公式，并保证其成为派生指标依赖的唯一来源。"""

    formula = metric.formula_definition or {}
    if not formula:
        return []
    operation = formula.get("operation")
    components = formula.get("components")
    if (
        operation not in {"RATIO", "SUM", "DIFFERENCE", "PRODUCT"}
        or not isinstance(components, list)
        or len(components) < 2
    ):
        return [
            _check(
                "METRIC_FORMULA", subject, "指标公式结构无效", "METRIC_FORMULA_INVALID"
            )
        ]
    refs = [item.get("metric_id") for item in components if isinstance(item, dict)]
    valid_refs = [item for item in refs if isinstance(item, int) and item > 0]
    known_ids = {item.id for item in metrics}
    checks: list[SemanticValidationCheck] = []
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("metric_id"), int)
        or item.get("metric_id", 0) <= 0
        or not isinstance(item.get("role"), str)
        or not item.get("role", "").strip()
        for item in components
    ):
        checks.append(
            _check(
                "METRIC_FORMULA_COMPONENT",
                subject,
                "指标公式组件必须声明正整数指标 ID 和角色",
                "METRIC_FORMULA_INVALID",
            )
        )
    if any(item not in known_ids for item in valid_refs) or len(valid_refs) != len(
        refs
    ):
        checks.append(
            _check(
                "METRIC_FORMULA_REFERENCE",
                subject,
                "指标公式引用的指标不存在",
                "SEMANTIC_CONTRACT_INCOMPLETE",
            )
        )
    if metric.id in valid_refs:
        checks.append(
            _check(
                "METRIC_FORMULA_CYCLE",
                subject,
                "指标公式不能引用自身",
                "METRIC_REFERENCE_CYCLE",
            )
        )
    if len(valid_refs) != len(set(valid_refs)):
        checks.append(
            _check(
                "METRIC_FORMULA_REFERENCE_DUPLICATED",
                subject,
                "指标公式不能重复引用同一指标",
                "METRIC_FORMULA_INVALID",
            )
        )
    if operation == "RATIO":
        roles = {item.get("role") for item in components if isinstance(item, dict)}
        if roles != {"numerator", "denominator"}:
            checks.append(
                _check(
                    "METRIC_FORMULA_ROLE",
                    subject,
                    "比率公式必须声明分子和分母",
                    "METRIC_FORMULA_INVALID",
                )
            )
    if operation == "DIFFERENCE":
        roles = {item.get("role") for item in components if isinstance(item, dict)}
        if roles != {"minuend", "subtrahend"}:
            checks.append(
                _check(
                    "METRIC_FORMULA_ROLE",
                    subject,
                    "差值公式必须声明被减数和减数",
                    "METRIC_FORMULA_INVALID",
                )
            )
    metric_by_id = {item.id: item for item in metrics}
    for ref in valid_refs:
        referenced = metric_by_id.get(ref)
        if referenced is not None and referenced.model_id != metric.model_id:
            checks.append(
                _check(
                    "METRIC_FORMULA_MODEL",
                    subject,
                    "指标公式组件必须属于同一模型",
                    "METRIC_FORMULA_CROSS_MODEL",
                )
            )
    if metric.metric_refs and list(metric.metric_refs) != valid_refs:
        checks.append(
            _check(
                "METRIC_FORMULA_LEGACY_REFERENCE",
                subject,
                "metric_refs 必须由结构化公式确定性投影",
                "METRIC_FORMULA_DUPLICATED_SOURCE",
            )
        )
    return checks


def _validate_metric_formula_cycles(
    metrics: list[SemanticMetric],
) -> list[SemanticValidationCheck]:
    """检查多级派生指标引用，避免只拦截直接自引用。"""

    graph: dict[int, tuple[int, ...]] = {}
    for metric in metrics:
        if metric.id is None:
            continue
        refs: list[int] = []
        for item in (metric.formula_definition or {}).get("components") or []:
            if isinstance(item, dict) and isinstance(item.get("metric_id"), int):
                refs.append(item["metric_id"])
        graph[metric.id] = tuple(refs)
    checks: list[SemanticValidationCheck] = []

    def reaches(start: int, target: int, visited: set[int]) -> bool:
        if start == target:
            return True
        if start in visited:
            return False
        visited.add(start)
        return any(reaches(item, target, visited) for item in graph.get(start, ()))

    for metric_id, component_refs in graph.items():
        if any(
            ref != metric_id and reaches(ref, metric_id, set())
            for ref in component_refs
        ):
            checks.append(
                _check(
                    "METRIC_FORMULA_CYCLE",
                    f"metric:{metric_id}",
                    "指标公式存在循环引用",
                    "METRIC_REFERENCE_CYCLE",
                )
            )
    return checks


def _validate_dimensions(
    dimensions: list[SemanticDimension],
    logical_dimensions: list[LogicalDimension],
    entities: list[BusinessEntity],
    models: list[SemanticModel],
) -> list[SemanticValidationCheck]:
    logical_ids = {item.id for item in logical_dimensions}
    entity_by_id = {item.id: item for item in entities}
    logical_by_id = {item.id: item for item in logical_dimensions}
    model_by_id = {item.id: item for item in models}
    binding_keys: set[tuple[int, int, str]] = set()
    checks: list[SemanticValidationCheck] = []
    for dimension in dimensions:
        subject = f"dimension:{dimension.id}"
        if dimension.logical_dimension_id not in logical_ids:
            checks.append(
                _check(
                    "DIMENSION_BINDING",
                    subject,
                    "物理维度必须绑定业务维度",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if dimension.logical_dimension_id is not None:
            logical = logical_by_id.get(dimension.logical_dimension_id)
            if (
                logical
                and logical.entity_id is not None
                and logical.entity_id not in entity_by_id
            ):
                checks.append(
                    _check(
                        "DIMENSION_ENTITY",
                        subject,
                        "业务维度引用的实体不存在",
                        "SEMANTIC_CONTRACT_INCOMPLETE",
                    )
                )
            if dimension.binding_role not in {"KEY", "ATTRIBUTE", "TIME"}:
                checks.append(
                    _check(
                        "DIMENSION_BINDING_ROLE",
                        subject,
                        "物理维度必须声明有效的绑定角色",
                        "SEMANTIC_CONTRACT_INCOMPLETE",
                    )
                )
            if not (dimension.field_id or dimension.field_name or dimension.expr):
                checks.append(
                    _check(
                        "DIMENSION_SOURCE",
                        subject,
                        "物理维度必须存在字段或表达式来源",
                        "SEMANTIC_CONTRACT_INCOMPLETE",
                    )
                )
            if logical:
                model = model_by_id.get(dimension.model_id)
                if model and model.domain_id != logical.domain_id:
                    checks.append(
                        _check(
                            "DIMENSION_LOGICAL_DOMAIN",
                            subject,
                            "物理维度与业务维度不属于同一主题域",
                            "SEMANTIC_CONTRACT_INCOMPLETE",
                        )
                    )
                if not _value_types_compatible(dimension.data_type, logical.value_type):
                    checks.append(
                        _check(
                            "DIMENSION_VALUE_TYPE",
                            subject,
                            "物理维度数据类型与业务维度值类型不兼容",
                            "SEMANTIC_CONTRACT_INCOMPLETE",
                        )
                    )
                binding_key = (
                    dimension.model_id,
                    dimension.logical_dimension_id,
                    dimension.binding_role or "",
                )
                if binding_key in binding_keys:
                    checks.append(
                        _check(
                            "DIMENSION_BINDING_DUPLICATED",
                            subject,
                            "同模型内存在重复的业务维度绑定角色",
                            "SEMANTIC_CONTRACT_INCOMPLETE",
                        )
                    )
                binding_keys.add(binding_key)
            if logical and logical.entity_id is not None:
                entity = entity_by_id.get(logical.entity_id)
                if entity and entity.domain_id != logical.domain_id:
                    checks.append(
                        _check(
                            "LOGICAL_DIMENSION_ENTITY_DOMAIN",
                            subject,
                            "业务维度与业务实体不属于同一主题域",
                            "SEMANTIC_CONTRACT_INCOMPLETE",
                        )
                    )
                if (
                    entity
                    and entity.value_domain_key
                    and logical.value_domain_key != entity.value_domain_key
                ):
                    checks.append(
                        _check(
                            "LOGICAL_DIMENSION_VALUE_DOMAIN",
                            subject,
                            "实体维度值域与业务实体不一致",
                            "SEMANTIC_CONTRACT_INCOMPLETE",
                        )
                    )
    return checks


def _validate_relations(
    relations: list[SemanticModelRelation],
    models: list[SemanticModel],
    model_fields: list[SemanticModelField],
) -> list[SemanticValidationCheck]:
    model_ids = {model.id for model in models}
    field_names_by_model: dict[int, set[str]] = {}
    for field in model_fields:
        field_names_by_model.setdefault(field.model_id, set()).update(
            {field.field_name, field.biz_name}
        )
    checks: list[SemanticValidationCheck] = []
    for relation in relations:
        subject = f"relation:{relation.id}"
        if (
            not relation.cardinality
            or not relation.metric_propagation
            or not relation.aggregation_safety
        ):
            checks.append(
                _check(
                    "RELATION_CONTRACT",
                    subject,
                    "关系必须声明基数、指标传播方向和聚合安全",
                    "RELATION_PATH_INVALID",
                )
            )
        if not relation.join_conditions:
            checks.append(
                _check(
                    "RELATION_JOIN_CONDITION",
                    subject,
                    "关系必须声明连接条件",
                    "RELATION_PATH_INVALID",
                )
            )
        elif not _join_conditions_valid(relation, field_names_by_model):
            checks.append(
                _check(
                    "RELATION_JOIN_FIELD",
                    subject,
                    "关系连接条件引用的字段不存在或操作符无效",
                    "RELATION_PATH_INVALID",
                )
            )
        if relation.cardinality not in {
            "ONE_TO_ONE",
            "ONE_TO_MANY",
            "MANY_TO_ONE",
            "MANY_TO_MANY",
        }:
            checks.append(
                _check(
                    "RELATION_CARDINALITY",
                    subject,
                    "关系基数取值无效",
                    "RELATION_PATH_INVALID",
                )
            )
        expected_uniqueness = (
            None
            if relation.cardinality is None
            else {
                "ONE_TO_ONE": (True, True),
                "ONE_TO_MANY": (True, False),
                "MANY_TO_ONE": (False, True),
                "MANY_TO_MANY": (False, False),
            }.get(relation.cardinality)
        )
        if (
            expected_uniqueness
            and (relation.left_unique, relation.right_unique) != expected_uniqueness
        ):
            checks.append(
                _check(
                    "RELATION_UNIQUENESS",
                    subject,
                    "关系唯一性声明与基数不一致",
                    "RELATION_PATH_INVALID",
                )
            )
        if (
            relation.left_model_id not in model_ids
            or relation.right_model_id not in model_ids
        ):
            checks.append(
                _check(
                    "RELATION_MODEL_REFERENCE",
                    subject,
                    "关系引用的模型不存在",
                    "RELATION_PATH_INVALID",
                )
            )
        if (
            relation.cardinality == "MANY_TO_MANY"
            and relation.aggregation_safety != "FORBIDDEN"
        ):
            checks.append(
                _check(
                    "RELATION_MANY_TO_MANY",
                    subject,
                    "多对多关系默认禁止承载指标",
                    "JOIN_CAUSES_METRIC_DUPLICATION",
                )
            )
    return checks


def _validate_capabilities(
    capabilities: list[MetricDimensionCapability],
    metrics: list[SemanticMetric],
    logical_dimensions: list[LogicalDimension],
    relations: list[SemanticModelRelation],
    models: list[SemanticModel],
    dimensions: list[SemanticDimension],
) -> list[SemanticValidationCheck]:
    metric_ids = {item.id for item in metrics}
    logical_ids = {item.id for item in logical_dimensions}
    relation_ids = {item.id for item in relations}
    model_ids = {item.id for item in models}
    dimension_by_id = {item.id: item for item in dimensions}
    metric_by_id = {item.id: item for item in metrics}
    relation_by_id = {item.id: item for item in relations}
    checks: list[SemanticValidationCheck] = []
    for capability in capabilities:
        subject = f"capability:{capability.id}"
        if (
            capability.metric_id not in metric_ids
            or capability.logical_dimension_id not in logical_ids
        ):
            checks.append(
                _check(
                    "CAPABILITY_REFERENCE",
                    subject,
                    "指标维度能力引用的指标或业务维度不存在",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if capability.target_model_id not in model_ids:
            checks.append(
                _check(
                    "CAPABILITY_TARGET_MODEL",
                    subject,
                    "指标维度能力的目标模型不存在",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if capability.physical_dimension_id is not None:
            physical_dimension = dimension_by_id.get(capability.physical_dimension_id)
            if physical_dimension is None:
                checks.append(
                    _check(
                        "CAPABILITY_PHYSICAL_DIMENSION",
                        subject,
                        "指标维度能力的物理维度不存在",
                        "SEMANTIC_CONTRACT_INCOMPLETE",
                    )
                )
            elif physical_dimension.model_id != capability.target_model_id:
                checks.append(
                    _check(
                        "CAPABILITY_PHYSICAL_MODEL",
                        subject,
                        "指标维度能力的物理维度不属于目标模型",
                        "SEMANTIC_CONTRACT_INCOMPLETE",
                    )
                )
            elif (
                physical_dimension.logical_dimension_id
                != capability.logical_dimension_id
            ):
                checks.append(
                    _check(
                        "CAPABILITY_LOGICAL_BINDING",
                        subject,
                        "能力契约与物理维度绑定的业务维度不一致",
                        "SEMANTIC_CONTRACT_INCOMPLETE",
                    )
                )
        if not capability.usages:
            checks.append(
                _check(
                    "CAPABILITY_USAGE",
                    subject,
                    "指标维度能力必须声明用途",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if any(
            usage not in {"GROUP_BY", "FILTER", "DETAIL", "CONTRIBUTION"}
            for usage in capability.usages
        ):
            checks.append(
                _check(
                    "CAPABILITY_USAGE_VALUE",
                    subject,
                    "指标维度能力用途取值无效",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if capability.contribution_tolerance < 0:
            checks.append(
                _check(
                    "CAPABILITY_CONTRIBUTION_TOLERANCE",
                    subject,
                    "贡献度对账容差不能为负数",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if capability.binding_strategy not in {"SAME_MODEL", "RELATION_PATH"}:
            checks.append(
                _check(
                    "CAPABILITY_BINDING_STRATEGY",
                    subject,
                    "指标维度能力绑定方式无效",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if capability.aggregation_safety not in {
            "SAFE",
            "PRE_AGGREGATE_REQUIRED",
            "FORBIDDEN",
        }:
            checks.append(
                _check(
                    "CAPABILITY_AGGREGATION_SAFETY",
                    subject,
                    "指标维度能力聚合安全取值无效",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if (
            capability.aggregation_safety == "PRE_AGGREGATE_REQUIRED"
            and not capability.pre_aggregation_grain
        ):
            checks.append(
                _check(
                    "CAPABILITY_PRE_AGGREGATION_GRAIN",
                    subject,
                    "预聚合能力必须声明预聚合粒度",
                    "METRIC_GRAIN_INCOMPATIBLE",
                )
            )
        metric = metric_by_id.get(capability.metric_id)
        if (
            capability.binding_strategy == "SAME_MODEL"
            and metric
            and (
                capability.relation_path
                or capability.target_model_id != metric.model_id
            )
        ):
            checks.append(
                _check(
                    "CAPABILITY_SAME_MODEL",
                    subject,
                    "同模型能力不能声明关系路径且目标模型必须是指标模型",
                    "RELATION_PATH_INVALID",
                )
            )
        if (
            capability.binding_strategy == "RELATION_PATH"
            and not capability.relation_path
        ):
            checks.append(
                _check(
                    "CAPABILITY_RELATION_PATH",
                    subject,
                    "跨模型能力必须声明关系路径",
                    "RELATION_PATH_INVALID",
                )
            )
        if any(item not in relation_ids for item in capability.relation_path):
            checks.append(
                _check(
                    "CAPABILITY_RELATION_REFERENCE",
                    subject,
                    "关系路径包含不存在的关系",
                    "RELATION_PATH_INVALID",
                )
            )
        elif capability.binding_strategy == "RELATION_PATH" and metric:
            current_model_id: int | None = metric.model_id
            for relation_id in capability.relation_path:
                relation = relation_by_id[relation_id]
                if relation.left_model_id == current_model_id:
                    current_model_id = relation.right_model_id
                elif relation.right_model_id == current_model_id:
                    current_model_id = relation.left_model_id
                else:
                    current_model_id = None
                    break
            if current_model_id != capability.target_model_id:
                checks.append(
                    _check(
                        "CAPABILITY_RELATION_CONTINUITY",
                        subject,
                        "关系路径不连续或未到达目标模型",
                        "RELATION_PATH_INVALID",
                    )
                )
    return checks


def _value_types_compatible(data_type: str | None, value_type: str) -> bool:
    if not data_type:
        return True
    normalized_data_type = data_type.upper()
    normalized_value_type = value_type.upper()
    families = [
        {"INT", "INTEGER", "BIGINT", "SMALLINT", "LONG"},
        {"DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL", "NUMBER"},
        {"CHAR", "VARCHAR", "TEXT", "STRING"},
        {"DATE", "TIME", "TIMESTAMP", "DATETIME"},
        {"BOOL", "BOOLEAN"},
    ]
    return normalized_data_type == normalized_value_type or any(
        any(token in normalized_data_type for token in family)
        and normalized_value_type in family
        for family in families
    )


def _join_conditions_valid(
    relation: SemanticModelRelation,
    field_names_by_model: dict[int, set[str]],
) -> bool:
    left_fields = field_names_by_model.get(relation.left_model_id)
    right_fields = field_names_by_model.get(relation.right_model_id)
    for item in relation.join_conditions:
        if not isinstance(item, dict):
            return False
        left = item.get("leftField") or item.get("left_field")
        right = item.get("rightField") or item.get("right_field")
        operator = str(item.get("operator") or "=").upper()
        if not left or not right or operator not in {"=", "IS NOT DISTINCT FROM"}:
            return False
        if left_fields is not None and left not in left_fields:
            return False
        if right_fields is not None and right not in right_fields:
            return False
    return True
