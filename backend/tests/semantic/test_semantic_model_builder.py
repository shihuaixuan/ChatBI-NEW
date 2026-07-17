from apps.semantic.models import SemanticMetric, SemanticModel
from apps.semantic.schemas import SemanticColumnMeta, ModelCreateWithAssetsPayload
from apps.semantic.service import (
    SemanticModelBuilder,
    build_metrics_from_model_measures,
    build_model_with_assets,
)


def test_model_builder_classifies_table_fields_into_model_detail():
    columns = [
        SemanticColumnMeta(field_name="stall_id", field_type="BIGINT", field_comment="档口"),
        SemanticColumnMeta(field_name="stat_date", field_type="DATE", field_comment="统计日期"),
        SemanticColumnMeta(field_name="visit_uv", field_type="BIGINT", field_comment="访问人数"),
    ]

    result = SemanticModelBuilder().build_table_schema(
        table_name="stall_traffic_1d",
        columns=columns,
        datasource_id=7,
    )

    assert [item["fieldName"] for item in result.model_detail["fields"]] == [
        "stall_id",
        "stat_date",
        "visit_uv",
    ]
    assert [item["bizName"] for item in result.model_detail["identifiers"]] == ["stall_id"]
    assert result.model_detail["identifiers"][0]["type"] == "primary"
    assert [item["bizName"] for item in result.model_detail["dimensions"]] == [
        "stall_id",
        "stat_date",
    ]
    assert result.model_detail["dimensions"][0]["type"] == "primary_key"
    assert result.model_detail["dimensions"][1]["type"] == "partition_time"
    assert result.model_detail["dimensions"][1]["dateFormat"] == "yyyy-MM-dd"
    assert result.model_detail["dimensions"][1]["typeParams"] == {
        "isPrimary": "true",
        "timeGranularity": "day",
    }
    assert result.model_detail["measures"][0]["bizName"] == "visit_uv"
    assert result.model_detail["measures"][0]["datasourceId"] == 7
    assert result.model_detail["measures"][0]["agg"] == "SUM"
    assert result.model_detail["measures"][0]["createMetric"] is False
    assert result.model_detail["measures"][0]["isCreateMetric"] == 0


def test_build_model_with_assets_generates_dimensions_but_keeps_metrics_manual():
    payload = ModelCreateWithAssetsPayload(
        domain_id=1,
        datasource_id=7,
        name="档口流量模型",
        biz_name="stall_traffic",
        source_type="TABLE",
        table_name="stall_traffic_1d",
        model_detail={
            "fields": [
                {"fieldName": "stall_id", "dataType": "BIGINT", "name": "档口"},
                {"fieldName": "visit_uv", "dataType": "BIGINT", "name": "访问人数"},
            ],
            "identifiers": [{"bizName": "stall_id", "name": "档口", "type": "primary"}],
            "dimensions": [{"bizName": "stall_id", "name": "档口", "expr": "stall_id", "dataType": "BIGINT"}],
            "measures": [{"bizName": "visit_uv", "name": "访问人数", "expr": "visit_uv", "agg": "SUM"}],
        },
    )

    bundle = build_model_with_assets(payload)

    assert bundle.model.model_detail["tableQuery"]["table"] == "stall_traffic_1d"
    assert [item.biz_name for item in bundle.dimensions] == ["stall_id"]
    assert bundle.metrics == []


def test_build_metrics_from_model_measures_creates_selected_and_skips_existing():
    model = SemanticModel(
        id=9,
        oid=88,
        domain_id=1,
        datasource_id=7,
        name="档口流量模型",
        biz_name="stall_traffic",
        model_detail={
            "measures": [
                {"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM", "alias": ["UV"]},
                {"name": "支付金额", "bizName": "pay_amt", "expr": "pay_amount", "agg": "SUM"},
            ]
        },
    )
    existing = [SemanticMetric(oid=88, model_id=9, name="访问人数", biz_name="visit_uv")]

    result = build_metrics_from_model_measures(
        model=model,
        oid=88,
        measure_biz_names=["visit_uv", "pay_amt"],
        existing_metrics=existing,
    )

    assert result.skipped == ["visit_uv"]
    assert [item.biz_name for item in result.metrics] == ["pay_amt"]
    metric = result.metrics[0]
    assert metric.name == "支付金额"
    assert metric.model_id == 9
    assert metric.default_agg == "SUM"
    assert metric.define_type == "MEASURE"
    assert metric.type_params == {
        "metricDefineType": "MEASURE",
        "metricDefineByMeasureParams": {
            "measures": [{"name": "支付金额", "bizName": "pay_amt", "expr": "pay_amount", "agg": "SUM", "datasourceId": 9}],
            "expr": "pay_amt",
            "filterSql": "",
        },
    }
