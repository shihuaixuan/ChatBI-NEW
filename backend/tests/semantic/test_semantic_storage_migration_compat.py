from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelMeasure,
    SemanticModelRelation,
)
from apps.semantic.repository.metric_repository import MetricDependencyFacts
from apps.semantic.repository.sqlmodel.storage_consistency import (
    check_storage_consistency,
)
from apps.semantic.repository.sqlmodel.storage_sync import (
    mark_domain_datasets_schema_changed,
    mark_model_schema_changed,
)
from apps.semantic.services.contract_backfill import (
    plan_semantic_contract_backfill,
)
from apps.semantic.services.rules.metric_quality import (
    validate_metric_dependencies,
    validate_metric_quality,
)


class _Result:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class _QueuedSession:
    def __init__(self, results):
        self.results = list(results)
        self.added = []
        self.exec_count = 0

    def exec(self, _statement):
        self.exec_count += 1
        if self.results:
            return _Result(self.results.pop(0))
        return _Result([])

    def add(self, item):
        self.added.append(item)


def test_storage_consistency_reports_model_and_dimension_mismatches():
    model = SemanticModel(
        id=1,
        oid=1,
        domain_id=1,
        datasource_id=1,
        name="模型",
        biz_name="model",
        model_detail={
            "fields": [{"bizName": "field_a"}],
            "measures": [{"bizName": "measure_a"}],
        },
    )
    dimension = SemanticDimension(
        id=2,
        oid=1,
        model_id=1,
        name="性别",
        biz_name="gender",
        dim_value_maps=[{"value": "女"}],
    )
    issues = check_storage_consistency(
        model=model,
        fields=[
            SemanticModelField(
                oid=1,
                model_id=1,
                field_name="field_b",
                name="字段B",
                biz_name="field_b",
                expr="field_b",
            )
        ],
        measures=[
            SemanticModelMeasure(
                oid=1, model_id=1, name="度量B", biz_name="measure_b", expr="measure_b"
            )
        ],
        dimension=dimension,
        dimension_values=[
            SemanticDimensionValue(oid=1, dimension_id=2, model_id=1, value="男")
        ],
    )

    assert {item["type"] for item in issues} == {
        "MODEL_FIELD_MISMATCH",
        "MODEL_MEASURE_MISMATCH",
        "DIMENSION_VALUE_MISMATCH",
    }


def test_metric_quality_marks_invalid_missing_expr_and_valid_metric():
    invalid = SemanticMetric(
        oid=1, model_id=1, name="坏指标", biz_name="bad_metric", fields=[]
    )
    valid = SemanticMetric(
        oid=1,
        model_id=1,
        name="好指标",
        biz_name="good_metric",
        expr="amount",
        fields=["amount"],
    )

    validate_metric_quality(invalid)
    validate_metric_quality(valid)

    assert invalid.quality_status == "INVALID"
    assert "表达式" in invalid.quality_message
    assert valid.quality_status == "VALID"
    assert valid.quality_message == ""


def test_metric_api_quality_validation_marks_missing_storage_fields():
    metric = SemanticMetric(
        oid=1,
        model_id=9,
        name="异常指标",
        biz_name="bad_metric",
        expr="missing_amount",
        fields=["missing_amount"],
        quality_status="VALID",
    )
    facts = MetricDependencyFacts(
        known_fields=frozenset({"amount"}),
    )

    validate_metric_dependencies(metric, facts)

    assert metric.quality_status == "INVALID"
    assert "依赖字段不存在" in metric.quality_message


def test_metric_api_quality_validation_accepts_existing_metric_dependencies():
    metric = SemanticMetric(
        oid=1,
        model_id=9,
        name="转化率",
        biz_name="conversion_rate",
        define_type="METRIC",
        expr="conversion_uv / visit_uv",
        fields=["conversion_uv", "visit_uv"],
        metric_refs=[11, 12],
        quality_status="VALID",
    )
    facts = MetricDependencyFacts(
        known_fields=frozenset({"event_id", "user_id"}),
        existing_metric_ids=frozenset({11, 12}),
    )

    validate_metric_dependencies(metric, facts)

    assert metric.quality_status == "VALID"
    assert metric.quality_message is None


def test_model_schema_change_marks_domain_datasets():
    dataset = SemanticDataset(
        id=3, oid=1, domain_id=2, name="数据集", biz_name="dataset", schema_version=5
    )
    session = _QueuedSession([[dataset]])

    mark_domain_datasets_schema_changed(session, oid=1, domain_id=2)

    assert dataset.schema_version == 6
    assert session.added == [dataset]
    assert session.exec_count == 1


def test_shared_model_schema_change_updates_model_and_domain_datasets():
    model = SemanticModel(
        id=1,
        oid=1,
        domain_id=2,
        datasource_id=7,
        name="模型",
        biz_name="model",
        schema_version=3,
    )
    dataset = SemanticDataset(
        id=3,
        oid=1,
        domain_id=2,
        name="数据集",
        biz_name="dataset",
        schema_version=5,
    )
    session = _QueuedSession([[dataset]])

    mark_model_schema_changed(session, model)

    assert model.schema_version == 4
    assert dataset.schema_version == 6
    assert session.added == [model, dataset]


def test_contract_backfill_only_applies_deterministic_values_and_reports_reviews():
    model = SemanticModel(
        id=1,
        oid=1,
        domain_id=2,
        datasource_id=3,
        name="旧模型",
        biz_name="legacy_model",
        primary_key=["id"],
    )
    dimension = SemanticDimension(
        id=2,
        oid=1,
        model_id=1,
        name="主键",
        biz_name="id",
        is_primary_key=True,
    )
    metric = SemanticMetric(
        id=3,
        oid=1,
        model_id=1,
        name="金额",
        biz_name="amount",
        relate_dimensions=[{"id": 2}],
    )
    relation = SemanticModelRelation(
        id=4,
        oid=1,
        domain_id=2,
        left_model_id=1,
        right_model_id=5,
    )
    dataset = SemanticDataset(
        id=6,
        oid=1,
        domain_id=2,
        name="旧数据集",
        biz_name="legacy_dataset",
    )

    plan = plan_semantic_contract_backfill(
        models=[model],
        metrics=[metric],
        dimensions=[dimension],
        relations=[relation],
        datasets=[dataset],
        dataset_model_configs=[],
        capabilities=[],
    )

    updates = {
        (item.asset_type, item.asset_id): item.values for item in plan.updates
    }
    assert updates[("MODEL", 1)] == {"contract_status": "DRAFT"}
    assert updates[("DIMENSION", 2)] == {
        "binding_role": "KEY",
        "binding_priority": 0,
    }
    assert updates[("RELATION", 4)] == {"contract_status": "DRAFT"}
    assert {item.code for item in plan.reviews} >= {
        "MODEL_KIND_REVIEW_REQUIRED",
        "LOGICAL_DIMENSION_REVIEW_REQUIRED",
        "METRIC_CONTRACT_REVIEW_REQUIRED",
        "RELATION_CONTRACT_REVIEW_REQUIRED",
    }
    assert plan.coverage[0].dimension_binding_rate == 0.0
