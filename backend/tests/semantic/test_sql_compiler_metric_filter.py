"""P0-6a 指标级 filter_sql 进编译：单指标 WHERE 合并、同口径去重、异构报错。"""

from __future__ import annotations

import pytest

from apps.semantic.models.dto import (
    DatasetSchema,
    SchemaElement,
    SemanticAggregationPlan,
    SemanticMetricBinding,
    SemanticModelPlan,
    SemanticPlanStatus,
    SemanticQueryPlan,
)
from apps.semantic.services.query.validation import SemanticQueryValidationService
from apps.semantic.services.sql_compiler import (
    SemanticSQLCompiler,
    SemanticSQLCompileRequest,
)


def _schema(metric_filters: dict[int, str]) -> DatasetSchema:
    return DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business",
            type="DATASET",
        ),
        models=[
            {
                "id": 10,
                "name": "销售模型",
                "biz_name": "sales_model",
                "tableQuery": "sales_order",
                "measures": [
                    {"name": "销售额", "bizName": "sales_amount", "expr": "amount", "agg": "SUM"},
                    {"name": "退货额", "bizName": "refund_amount", "expr": "refund_amount", "agg": "SUM"},
                ],
            }
        ],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=metric_id,
                name=name,
                biz_name=biz_name,
                type="METRIC",
                default_agg="SUM",
                fields=[biz_name],
                ext_info={"filter_sql": filter_sql} if filter_sql else {},
            )
            for metric_id, name, biz_name, filter_sql in (
                (100, "销售额", "sales_amount", metric_filters.get(100, "")),
                (101, "有效销售额", "effective_sales", metric_filters.get(101, "")),
                (102, "退货额", "refund_amount", metric_filters.get(102, "")),
            )
        ],
    )


def test_single_metric_filter_merges_into_where_with_source_metadata():
    schema = _schema({100: "order_status = 'PAID'"})

    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=schema,
            metric_ids=[100],
        )
    )

    assert "where" in result.sql
    assert "sales_model.order_status = 'PAID'" in result.sql
    assert result.metric_filters == [
        {
            "metric_id": 100,
            "biz_name": "sales_amount",
            "model": "sales_model",
            "filter_sql": "order_status = 'PAID'",
        }
    ]


def test_metrics_with_identical_filters_merge_once():
    schema = _schema({100: "order_status = 'PAID'", 101: "order_status = 'PAID'"})

    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=schema,
            metric_ids=[100, 101],
        )
    )

    assert result.sql.count("sales_model.order_status = 'PAID'") == 1
    assert len(result.metric_filters) == 2


def test_metric_without_filter_keeps_sql_unchanged():
    schema = _schema({})

    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=schema,
            metric_ids=[100],
        )
    )

    assert "where" not in result.sql
    assert result.metric_filters == []


def test_compiler_rejects_conflicting_metric_filters():
    schema = _schema({100: "order_status = 'PAID'", 101: "order_status = 'VALID'"})

    with pytest.raises(ValueError, match="SEMANTIC_SQL_METRIC_FILTER_CONFLICT"):
        SemanticSQLCompiler().compile(
            SemanticSQLCompileRequest(
                schema=schema,
                metric_ids=[100, 101],
            )
        )


def test_qualifies_unqualified_filter_columns_with_model_alias():
    schema = _schema({100: "order_status = 'PAID' and channel = 'ONLINE'"})

    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=schema,
            metric_ids=[100],
        )
    )

    assert "sales_model.order_status = 'PAID'" in result.sql
    assert "sales_model.channel = 'ONLINE'" in result.sql


def _plan(metric_ids: list[int]) -> SemanticQueryPlan:
    return SemanticQueryPlan(
        plan_id="plan-1",
        dataset_id=20,
        schema_version=1,
        contract_version=1,
        metrics=tuple(
            SemanticMetricBinding(
                metric_id=metric_id,
                model_id=10,
                version=1,
                aggregation="SUM",
                additivity="FULL",
            )
            for metric_id in metric_ids
        ),
        model_plan=SemanticModelPlan(base_model_id=10, model_ids=(10,)),
        aggregation_plan=SemanticAggregationPlan(
            aggregations=dict.fromkeys(metric_ids, "SUM")
        ),
        validation_status=SemanticPlanStatus.PROVEN,
        fingerprint="plan-fingerprint",
    )


def test_plan_validation_reports_metric_filter_conflict():
    schema = _schema({100: "order_status = 'PAID'", 101: "order_status = 'VALID'"})

    report = SemanticQueryValidationService().validate(_plan([100, 101]), schema)

    assert "METRIC_FILTER_CONFLICT" in report.reason_codes


def test_plan_validation_accepts_consistent_metric_filters():
    schema = _schema({100: "order_status = 'PAID'", 101: "order_status = 'PAID'"})

    report = SemanticQueryValidationService().validate(_plan([100, 101]), schema)

    assert "METRIC_FILTER_CONFLICT" not in report.reason_codes
