from apps.headless.schemas import DataSetSchema, SchemaElement
from apps.headless.sql_compiler import SemanticSQLCompiler, SemanticSQLCompileRequest


def _schema() -> DataSetSchema:
    return DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        models=[
            {
                "id": 10,
                "name": "经营模型",
                "biz_name": "business_model",
                "tableQuery": "business_daily",
                "dimensions": [{"name": "统计日期", "bizName": "stat_date", "expr": "stat_date"}],
                "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
            }
        ],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=100,
                name="访问人数",
                biz_name="visit_uv",
                type="METRIC",
                fields=["visit_uv"],
                default_agg="SUM",
            )
        ],
        dimensions=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=200,
                name="统计日期",
                biz_name="stat_date",
                type="DIMENSION",
                ext_info={"dimension_type": "time"},
            )
        ],
    )


def test_sql_compiler_renders_single_date_time_filter():
    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=_schema(),
            slots={
                "metrics": [{"asset_type": "METRIC", "asset_id": 100}],
                "filters": [
                    {
                        "asset_type": "DIMENSION",
                        "asset_id": 200,
                        "operator": "=",
                        "value": {
                            "kind": "single_date",
                            "anchor": "today",
                            "offset_days": 0,
                            "timezone": "Asia/Shanghai",
                        },
                    }
                ],
            },
        )
    )

    assert "business_model.stat_date = CURRENT_DATE" in result.sql


def test_sql_compiler_renders_recent_days_time_filter_as_range():
    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=_schema(),
            slots={
                "metrics": [{"asset_type": "METRIC", "asset_id": 100}],
                "filters": [
                    {
                        "asset_type": "DIMENSION",
                        "asset_id": 200,
                        "operator": "=",
                        "value": {
                            "kind": "relative_range",
                            "unit": "day",
                            "amount": 7,
                            "anchor": "today",
                            "include_current": True,
                            "timezone": "Asia/Shanghai",
                        },
                    }
                ],
            },
        )
    )

    assert "business_model.stat_date >= DATE_SUB(CURRENT_DATE, INTERVAL 6 DAY)" in result.sql
    assert "business_model.stat_date <= CURRENT_DATE" in result.sql


def test_sql_compiler_renders_absolute_month_as_left_closed_right_open_range():
    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=_schema(),
            slots={
                "metrics": [{"asset_type": "METRIC", "asset_id": 100}],
                "filters": [
                    {
                        "asset_type": "DIMENSION",
                        "asset_id": 200,
                        "operator": "=",
                        "value": {
                            "kind": "absolute_range",
                            "start": "2026-06-01",
                            "end_exclusive": "2026-07-01",
                            "timezone": "Asia/Shanghai",
                        },
                    }
                ],
            },
        )
    )

    assert "business_model.stat_date >= '2026-06-01'" in result.sql
    assert "business_model.stat_date < '2026-07-01'" in result.sql
