from apps.semantic.models import (
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelMeasure,
)
from apps.semantic.storage_sync import check_storage_consistency


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


def test_storage_consistency_reports_json_and_storage_mismatches():
    model = SemanticModel(
        id=1,
        oid=1,
        domain_id=1,
        datasource_id=1,
        name="模型",
        biz_name="model",
        model_detail={"fields": [{"bizName": "field_a"}], "measures": [{"bizName": "measure_a"}]},
    )
    dimension = SemanticDimension(
        id=2,
        oid=1,
        model_id=1,
        name="性别",
        biz_name="gender",
        dim_value_maps=[{"value": "女"}],
    )
    dataset = SemanticDataset(
        id=3,
        oid=1,
        domain_id=1,
        name="数据集",
        biz_name="dataset",
        data_set_detail={"dataSetModelConfigs": [{"id": 1, "includesAll": False, "metrics": [10], "dimensions": [2]}]},
    )

    issues = check_storage_consistency(
        model=model,
        fields=[SemanticModelField(oid=1, model_id=1, field_name="field_b", name="字段B", biz_name="field_b", expr="field_b")],
        measures=[SemanticModelMeasure(oid=1, model_id=1, name="度量B", biz_name="measure_b", expr="measure_b")],
        dimension=dimension,
        dimension_values=[SemanticDimensionValue(oid=1, dimension_id=2, model_id=1, value="男")],
        dataset=dataset,
        dataset_model_configs=[SemanticDatasetModelConfig(oid=1, dataset_id=3, model_id=2)],
        dataset_assets=[SemanticDatasetAsset(oid=1, dataset_id=3, model_id=1, asset_type="METRIC", asset_id=11)],
    )

    assert {item["type"] for item in issues} == {
        "MODEL_FIELD_MISMATCH",
        "MODEL_MEASURE_MISMATCH",
        "DIMENSION_VALUE_MISMATCH",
        "DATASET_MODEL_CONFIG_MISMATCH",
        "DATASET_ASSET_MISMATCH",
    }


def test_metric_quality_marks_invalid_missing_expr_and_valid_metric():
    invalid = SemanticMetric(oid=1, model_id=1, name="坏指标", biz_name="bad_metric", fields=[])
    valid = SemanticMetric(oid=1, model_id=1, name="好指标", biz_name="good_metric", expr="amount", fields=["amount"])

    from apps.semantic.storage_sync import validate_metric_quality

    validate_metric_quality(invalid)
    validate_metric_quality(valid)

    assert invalid.quality_status == "INVALID"
    assert "表达式" in invalid.quality_message
    assert valid.quality_status == "VALID"
    assert valid.quality_message == ""


def test_metric_api_quality_validation_marks_missing_storage_fields():
    from apps.semantic.api import _validate_metric_storage_quality

    metric = SemanticMetric(
        oid=1,
        model_id=9,
        name="异常指标",
        biz_name="bad_metric",
        expr="missing_amount",
        fields=["missing_amount"],
        quality_status="VALID",
    )
    session = _QueuedSession(
        [
            [SemanticModelField(oid=1, model_id=9, field_name="amount", name="金额", biz_name="amount", expr="amount")],
        ]
    )

    _validate_metric_storage_quality(session, metric)

    assert metric.quality_status == "INVALID"
    assert "依赖字段不存在" in metric.quality_message


def test_model_schema_change_marks_domain_datasets_and_clears_indexes():
    from apps.semantic.api import _mark_domain_datasets_schema_changed

    dataset = SemanticDataset(id=3, oid=1, domain_id=2, name="数据集", biz_name="dataset", schema_version=5)
    session = _QueuedSession([[dataset], [dataset.id], []])

    _mark_domain_datasets_schema_changed(session, oid=1, domain_id=2)

    assert dataset.schema_version == 6
    assert session.added == [dataset]
    assert session.exec_count == 3
