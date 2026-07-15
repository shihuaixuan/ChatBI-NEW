from apps.data_training.models.data_training_model import DataTraining
from apps.semantic.assets.quality_service import AssetQualityService
from apps.semantic.models.semantic_model import (
    DimensionType,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticMetric,
    SemanticType,
)
from apps.terminology.models.terminology_model import Terminology


def test_metric_quality_reports_missing_definition():
    metric = SemanticMetric(
        id=11,
        oid=1,
        datasource_id=4,
        name="amount",
        display_name="额度",
        aliases=[],
        description=None,
        expr="",
    )

    result = AssetQualityService().check_metric(metric)

    codes = {issue.code for issue in result.issues}
    assert "metric.description_missing" in codes
    assert "metric.expression_missing" in codes
    assert result.score < 1


def test_dimension_value_quality_reports_missing_display_value():
    value = SemanticDimensionValue(id=31, dimension_id=21, value="", display_value=None)

    result = AssetQualityService().check_dimension_value(value)

    codes = {issue.code for issue in result.issues}
    assert "dimension_value.value_missing" in codes
    assert "dimension_value.display_value_missing" in codes


def test_dimension_value_quality_reports_missing_dimension():
    value = SemanticDimensionValue(id=31, value="3", display_value="3号档口")

    result = AssetQualityService().check_dimension_value(value)

    assert any(issue.code == "dimension_value.dimension_missing" for issue in result.issues)


def test_dimension_quality_warns_unknown_semantic_type():
    dimension = SemanticDimension(
        id=21,
        oid=1,
        datasource_id=4,
        name="customer_id",
        display_name="客户ID",
        expr="customer_id",
    )

    result = AssetQualityService().check_dimension(dimension)

    assert any(issue.code == "dimension.semantic_type_unknown" for issue in result.issues)


def test_terminology_quality_reports_missing_mapping():
    term = Terminology(id=41, oid=1, word="人气", description="访问相关表现", enabled=True)

    result = AssetQualityService().check_term(term)

    assert any(issue.code == "term.mapped_assets_missing" for issue in result.issues)


def test_example_quality_reports_missing_linked_assets():
    example = DataTraining(id=51, oid=1, datasource=4, question="今天人气怎么样", enabled=True)

    result = AssetQualityService().check_example(example)

    assert any(issue.code == "example.linked_assets_missing" for issue in result.issues)


def test_dataset_quality_warns_similar_metrics_without_difference_description():
    first = SemanticMetric(
        id=11,
        oid=1,
        datasource_id=4,
        name="sales_quota",
        display_name="销售额度",
        aliases=["额度"],
        description="",
        expr="sum(sales_quota)",
    )
    second = SemanticMetric(
        id=12,
        oid=1,
        datasource_id=4,
        name="order_quota",
        display_name="订单额度",
        aliases=["额度"],
        description="",
        expr="sum(order_quota)",
    )

    results = AssetQualityService().check_dataset_assets([first, second])

    assert any(
        issue.code == "metric.similar_metric_missing_difference"
        for result in results
        for issue in result.issues
    )


def test_dataset_quality_warns_metric_without_default_time_dimension():
    metric = SemanticMetric(
        id=11,
        oid=1,
        datasource_id=4,
        name="visit_uv",
        display_name="访问人数",
        description="去重访问用户数",
        expr="count(distinct user_id)",
    )

    results = AssetQualityService().check_dataset_assets([metric])

    assert any(
        issue.code == "metric.default_time_dimension_missing"
        for result in results
        for issue in result.issues
    )


def test_dataset_quality_warns_multiple_default_time_dimensions():
    first = SemanticDimension(
        id=21,
        oid=1,
        datasource_id=4,
        name="biz_date",
        display_name="业务日期",
        expr="biz_date",
        dimension_type=DimensionType.TIME.value,
        semantic_type=SemanticType.DATE.value,
        is_default_time=True,
        time_granularities=["day"],
    )
    second = SemanticDimension(
        id=22,
        oid=1,
        datasource_id=4,
        name="pay_date",
        display_name="支付日期",
        expr="pay_date",
        dimension_type=DimensionType.TIME.value,
        semantic_type=SemanticType.DATE.value,
        is_default_time=True,
        time_granularities=["day"],
    )

    results = AssetQualityService().check_dataset_assets([first, second])

    assert any(
        issue.code == "dimension.default_time_not_unique"
        for result in results
        for issue in result.issues
    )


def test_dataset_quality_warns_high_cardinality_dimension_value():
    dimension = SemanticDimension(
        id=21,
        oid=1,
        datasource_id=4,
        name="customer_id",
        display_name="客户ID",
        expr="customer_id",
        dimension_type=DimensionType.ID.value,
    )
    value = SemanticDimensionValue(id=31, dimension_id=21, value="1000001", display_value="1000001")

    results = AssetQualityService().check_dataset_assets([dimension, value])

    assert any(
        issue.code == "dimension_value.high_cardinality_index_risk"
        for result in results
        for issue in result.issues
    )


def test_sql_example_quality_warns_unparsable_sql():
    example = DataTraining(
        id=51,
        oid=1,
        datasource=4,
        question="今天人气怎么样",
        enabled=True,
        example_type="SQL_EXAMPLE",
        sql="不是 SQL",
        linked_assets=[{"asset_type": "METRIC", "asset_id": 11}],
    )

    result = AssetQualityService().check_example(example)

    assert any(issue.code == "example.sql_unparsable" for issue in result.issues)
