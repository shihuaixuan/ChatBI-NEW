"""完整语义契约的集中式发布校验规则。"""

from collections.abc import Iterable

from apps.semantic.models.dto.semantic_validation import (
    SemanticValidationCheck,
)
from apps.semantic.models.orm import (
    BusinessEntity,
    LogicalDimension,
    MetricDimensionCapability,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelRelation,
)


def validate_semantic_contracts(
    models: Iterable[SemanticModel],
    metrics: Iterable[SemanticMetric],
    dimensions: Iterable[SemanticDimension],
    relations: Iterable[SemanticModelRelation],
    entities: Iterable[BusinessEntity],
    logical_dimensions: Iterable[LogicalDimension],
    capabilities: Iterable[MetricDimensionCapability],
    model_fields: Iterable[SemanticModelField] = (),
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
    checks: list[SemanticValidationCheck] = []
    checks.extend(_validate_models(model_list, model_field_list))
    checks.extend(
        _validate_metrics(
            metric_list,
            model_list,
            dimension_list,
            model_field_list,
        )
    )
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
        if model.model_kind not in {
            "ENTITY", "FACT", "DETAIL", "SNAPSHOT", "BRIDGE"
        }:
            checks.append(
                _check(
                    "MODEL_KIND",
                    subject,
                    "模型必须声明有效的模型类型",
                    "SEMANTIC_CONTRACT_INCOMPLETE",
                )
            )
        if model.model_kind in {"FACT", "DETAIL", "SNAPSHOT"} and not model.model_grain:
            checks.append(_check("MODEL_GRAIN", subject, "事实、明细和快照模型必须声明粒度", "SEMANTIC_CONTRACT_INCOMPLETE"))
        if model.model_kind == "ENTITY" and not model.primary_key:
            checks.append(_check("MODEL_PRIMARY_KEY", subject, "实体模型必须声明主键", "SEMANTIC_CONTRACT_INCOMPLETE"))
        if model.model_kind == "SNAPSHOT" and not model.snapshot_time_field:
            checks.append(_check("MODEL_SNAPSHOT_TIME", subject, "快照模型必须声明快照时间字段", "SEMANTIC_CONTRACT_INCOMPLETE"))
        if not model.row_description:
            checks.append(_check("MODEL_ROW_DESCRIPTION", subject, "模型必须声明一行数据的业务含义", "SEMANTIC_CONTRACT_INCOMPLETE"))
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
            checks.append(_check("METRIC_MODEL", subject, "指标基础模型不存在", "SEMANTIC_CONTRACT_INCOMPLETE"))
        if not metric.result_grain:
            checks.append(_check("METRIC_GRAIN", subject, "指标必须声明结果粒度", "METRIC_GRAIN_INCOMPATIBLE"))
        if not metric.additivity:
            checks.append(_check("METRIC_ADDITIVITY", subject, "指标必须声明可加性", "METRIC_NOT_ADDITIVE"))
        if not metric.description:
            checks.append(_check("METRIC_DESCRIPTION", subject, "指标必须声明业务口径", "SEMANTIC_CONTRACT_INCOMPLETE"))
        if metric.default_agg not in {
            "SUM", "COUNT", "COUNT_DISTINCT", "AVG", "MIN", "MAX", "CUSTOM"
        }:
            checks.append(_check("METRIC_DEFAULT_AGG", subject, "指标必须声明有效的默认聚合方式", "SEMANTIC_CONTRACT_INCOMPLETE"))
        if metric.additivity not in {"FULL", "SEMI", "NON_ADDITIVE"}:
            checks.append(_check("METRIC_ADDITIVITY_VALUE", subject, "指标可加性取值无效", "METRIC_NOT_ADDITIVE"))
        if metric.time_semantics not in {"EVENT", "SNAPSHOT", "PERIODIC_SNAPSHOT", "NONE"}:
            checks.append(_check("METRIC_TIME_SEMANTICS", subject, "指标必须声明有效的时间语义", "TIME_SEMANTICS_INCOMPATIBLE"))
        if metric.default_agg == "COUNT_DISTINCT" and not metric.distinct_keys:
            checks.append(_check("METRIC_DISTINCT_KEYS", subject, "去重指标必须声明去重键", "SEMANTIC_CONTRACT_INCOMPLETE"))
        if metric.time_semantics and metric.time_semantics != "NONE" and metric.default_time_dimension_id is None:
            checks.append(_check("METRIC_TIME_DIMENSION", subject, "时间指标必须声明默认时间维度", "TIME_SEMANTICS_INCOMPATIBLE"))
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
        if metric.time_semantics in {"SNAPSHOT", "PERIODIC_SNAPSHOT"} and not metric.snapshot_aggregation:
            checks.append(_check("METRIC_SNAPSHOT_AGGREGATION", subject, "快照指标必须声明快照聚合策略", "METRIC_NOT_ADDITIVE_OVER_TIME"))
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
            checks.append(_check("DIMENSION_BINDING", subject, "物理维度必须绑定业务维度", "SEMANTIC_CONTRACT_INCOMPLETE"))
        if dimension.logical_dimension_id is not None:
            logical = logical_by_id.get(dimension.logical_dimension_id)
            if logical and logical.entity_id is not None and logical.entity_id not in entity_by_id:
                checks.append(_check("DIMENSION_ENTITY", subject, "业务维度引用的实体不存在", "SEMANTIC_CONTRACT_INCOMPLETE"))
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
        if not relation.cardinality or not relation.metric_propagation or not relation.aggregation_safety:
            checks.append(_check("RELATION_CONTRACT", subject, "关系必须声明基数、指标传播方向和聚合安全", "RELATION_PATH_INVALID"))
        if not relation.join_conditions:
            checks.append(_check("RELATION_JOIN_CONDITION", subject, "关系必须声明连接条件", "RELATION_PATH_INVALID"))
        elif not _join_conditions_valid(relation, field_names_by_model):
            checks.append(_check("RELATION_JOIN_FIELD", subject, "关系连接条件引用的字段不存在或操作符无效", "RELATION_PATH_INVALID"))
        if relation.cardinality not in {"ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE", "MANY_TO_MANY"}:
            checks.append(_check("RELATION_CARDINALITY", subject, "关系基数取值无效", "RELATION_PATH_INVALID"))
        expected_uniqueness = None if relation.cardinality is None else {
            "ONE_TO_ONE": (True, True),
            "ONE_TO_MANY": (True, False),
            "MANY_TO_ONE": (False, True),
            "MANY_TO_MANY": (False, False),
        }.get(relation.cardinality)
        if expected_uniqueness and (relation.left_unique, relation.right_unique) != expected_uniqueness:
            checks.append(_check("RELATION_UNIQUENESS", subject, "关系唯一性声明与基数不一致", "RELATION_PATH_INVALID"))
        if relation.left_model_id not in model_ids or relation.right_model_id not in model_ids:
            checks.append(
                _check(
                    "RELATION_MODEL_REFERENCE",
                    subject,
                    "关系引用的模型不存在",
                    "RELATION_PATH_INVALID",
                )
            )
        if relation.cardinality == "MANY_TO_MANY" and relation.aggregation_safety != "FORBIDDEN":
            checks.append(_check("RELATION_MANY_TO_MANY", subject, "多对多关系默认禁止承载指标", "JOIN_CAUSES_METRIC_DUPLICATION"))
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
        if capability.metric_id not in metric_ids or capability.logical_dimension_id not in logical_ids:
            checks.append(_check("CAPABILITY_REFERENCE", subject, "指标维度能力引用的指标或业务维度不存在", "SEMANTIC_CONTRACT_INCOMPLETE"))
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
            elif physical_dimension.logical_dimension_id != capability.logical_dimension_id:
                checks.append(
                    _check(
                        "CAPABILITY_LOGICAL_BINDING",
                        subject,
                        "能力契约与物理维度绑定的业务维度不一致",
                        "SEMANTIC_CONTRACT_INCOMPLETE",
                    )
                )
        if not capability.usages:
            checks.append(_check("CAPABILITY_USAGE", subject, "指标维度能力必须声明用途", "SEMANTIC_CONTRACT_INCOMPLETE"))
        if any(usage not in {"GROUP_BY", "FILTER", "DETAIL"} for usage in capability.usages):
            checks.append(_check("CAPABILITY_USAGE_VALUE", subject, "指标维度能力用途取值无效", "SEMANTIC_CONTRACT_INCOMPLETE"))
        if capability.binding_strategy not in {"SAME_MODEL", "RELATION_PATH"}:
            checks.append(_check("CAPABILITY_BINDING_STRATEGY", subject, "指标维度能力绑定方式无效", "SEMANTIC_CONTRACT_INCOMPLETE"))
        if capability.aggregation_safety not in {"SAFE", "PRE_AGGREGATE_REQUIRED", "FORBIDDEN"}:
            checks.append(_check("CAPABILITY_AGGREGATION_SAFETY", subject, "指标维度能力聚合安全取值无效", "SEMANTIC_CONTRACT_INCOMPLETE"))
        if capability.aggregation_safety == "PRE_AGGREGATE_REQUIRED" and not capability.pre_aggregation_grain:
            checks.append(_check("CAPABILITY_PRE_AGGREGATION_GRAIN", subject, "预聚合能力必须声明预聚合粒度", "METRIC_GRAIN_INCOMPATIBLE"))
        metric = metric_by_id.get(capability.metric_id)
        if capability.binding_strategy == "SAME_MODEL" and metric and (
            capability.relation_path or capability.target_model_id != metric.model_id
        ):
            checks.append(_check("CAPABILITY_SAME_MODEL", subject, "同模型能力不能声明关系路径且目标模型必须是指标模型", "RELATION_PATH_INVALID"))
        if capability.binding_strategy == "RELATION_PATH" and not capability.relation_path:
            checks.append(_check("CAPABILITY_RELATION_PATH", subject, "跨模型能力必须声明关系路径", "RELATION_PATH_INVALID"))
        if any(item not in relation_ids for item in capability.relation_path):
            checks.append(_check("CAPABILITY_RELATION_REFERENCE", subject, "关系路径包含不存在的关系", "RELATION_PATH_INVALID"))
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
