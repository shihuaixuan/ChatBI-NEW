from apps.semantic.models import SemanticMetric, SemanticModel, SemanticModelMeasure
from apps.semantic.schemas import ModelCreateWithAssetsPayload
from apps.semantic.service import (
    build_metrics_from_model_measures,
    build_model_with_assets,
)
from apps.semantic.storage_sync import (
    build_model_detail_from_storage,
    model_fields_from_detail,
    model_measures_from_detail,
    normalize_model_storage_fields,
)


def test_model_detail_sync_extracts_fields_and_measures():
    model = SemanticModel(
        id=9,
        oid=1,
        domain_id=1,
        datasource_id=7,
        name="订单模型",
        biz_name="order_model",
        model_detail={
            "fields": [
                {"fieldName": "order_id", "dataType": "BIGINT", "name": "订单ID"},
                {"fieldName": "pay_amount", "dataType": "DECIMAL", "name": "支付金额"},
            ],
            "dimensions": [{"bizName": "order_id"}],
            "measures": [{"bizName": "pay_amount", "name": "支付金额", "expr": "pay_amount", "agg": "SUM"}],
        },
    )

    fields = model_fields_from_detail(model)
    measures = model_measures_from_detail(model, fields_by_biz_name={item.biz_name: item for item in fields})

    assert [(item.biz_name, item.field_role) for item in fields] == [
        ("order_id", "DIMENSION"),
        ("pay_amount", "MEASURE"),
    ]
    assert measures[0].biz_name == "pay_amount"
    assert measures[0].field_id is None
    assert measures[0].expr == "pay_amount"


def test_build_model_detail_from_storage_keeps_json_compatible_shape():
    model = SemanticModel(
        id=9,
        oid=1,
        domain_id=1,
        datasource_id=7,
        name="订单模型",
        biz_name="order_model",
        source_type="TABLE",
        table_name="orders",
        model_detail={"queryType": "table_query"},
    )
    detail = build_model_detail_from_storage(
        model,
        fields=[],
        measures=[
            SemanticModelMeasure(
                id=20,
                oid=1,
                model_id=9,
                name="支付金额",
                biz_name="pay_amount",
                expr="pay_amount",
                agg="SUM",
            )
        ],
    )

    assert detail["tableQuery"] == {"table": "orders"}
    assert detail["measures"][0]["id"] == 20
    assert detail["measures"][0]["bizName"] == "pay_amount"


def test_build_model_with_assets_normalizes_table_and_sql_fields():
    table_payload = ModelCreateWithAssetsPayload(
        domain_id=1,
        datasource_id=7,
        name="客户模型",
        biz_name="customer_model",
        source_type="TABLE",
        table_name="customers",
        model_detail={},
    )
    sql_payload = ModelCreateWithAssetsPayload(
        domain_id=1,
        datasource_id=7,
        name="客户SQL模型",
        biz_name="customer_sql_model",
        source_type="SQL",
        sql="select * from customers",
        model_detail={},
    )

    table_bundle = build_model_with_assets(table_payload)
    sql_bundle = build_model_with_assets(sql_payload)

    assert table_bundle.model.table_name == "customers"
    assert table_bundle.model.model_detail["tableQuery"]["table"] == "customers"
    assert sql_bundle.model.sql_query == "select * from customers"
    assert sql_bundle.model.model_detail["sqlQuery"]["sql"] == "select * from customers"


def test_build_metrics_from_model_measures_prefers_storage_measures_and_sets_fields():
    model = SemanticModel(
        id=9,
        oid=1,
        domain_id=1,
        datasource_id=7,
        name="订单模型",
        biz_name="order_model",
        model_detail={},
    )
    measure = SemanticModelMeasure(
        id=20,
        oid=1,
        model_id=9,
        name="支付金额",
        biz_name="pay_amount",
        expr="pay_amount",
        agg="SUM",
    )

    result = build_metrics_from_model_measures(
        model=model,
        oid=1,
        measure_ids=[20],
        storage_measures=[measure],
        existing_metrics=[],
    )

    metric = result.metrics[0]
    assert metric.measure_id == 20
    assert metric.expr == "pay_amount"
    assert metric.fields == ["pay_amount"]
    assert metric.type_params["metricDefineByMeasureParams"]["measureId"] == 20


def test_metric_fields_are_normalized_from_type_params():
    metric = SemanticMetric(
        oid=1,
        model_id=9,
        name="支付金额",
        biz_name="pay_amount",
        type_params={
            "metricDefineType": "MEASURE",
            "metricDefineByMeasureParams": {
                "measures": [{"bizName": "pay_amount", "expr": "pay_amount", "agg": "SUM"}],
            },
        },
    )

    normalize_model_storage_fields(metric)

    assert metric.expr == "pay_amount"
    assert metric.fields == ["pay_amount"]
