from datetime import date

import pytest

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


def _compile_time_filter(value: dict, today: date = date(2026, 7, 4)) -> str:
    result = SemanticSQLCompiler(today_provider=lambda timezone: today).compile(
        SemanticSQLCompileRequest(
            schema=_schema(),
            slots={
                "metrics": [{"asset_type": "METRIC", "asset_id": 100}],
                "filters": [
                    {"asset_type": "DIMENSION", "asset_id": 200, "operator": "=", "value": value}
                ],
            },
        )
    )
    return result.sql


def test_sql_compiler_renders_current_month_as_calendar_range():
    sql = _compile_time_filter({"kind": "current_period", "unit": "month", "timezone": "Asia/Shanghai"})

    assert "business_model.stat_date >= '2026-07-01'" in sql
    assert "business_model.stat_date < '2026-08-01'" in sql


def test_sql_compiler_renders_previous_month_as_calendar_range():
    sql = _compile_time_filter({"kind": "previous_period", "unit": "month", "timezone": "Asia/Shanghai"})

    assert "business_model.stat_date >= '2026-06-01'" in sql
    assert "business_model.stat_date < '2026-07-01'" in sql


def test_sql_compiler_renders_current_week_from_monday():
    # 2026-07-04 是周六，本周从周一 2026-06-29 开始。
    sql = _compile_time_filter({"kind": "current_period", "unit": "week", "timezone": "Asia/Shanghai"})

    assert "business_model.stat_date >= '2026-06-29'" in sql
    assert "business_model.stat_date < '2026-07-06'" in sql


def test_sql_compiler_renders_previous_quarter_as_calendar_range():
    sql = _compile_time_filter({"kind": "previous_period", "unit": "quarter", "timezone": "Asia/Shanghai"})

    assert "business_model.stat_date >= '2026-04-01'" in sql
    assert "business_model.stat_date < '2026-07-01'" in sql


def test_sql_compiler_renders_current_year_as_calendar_range():
    sql = _compile_time_filter({"kind": "current_period", "unit": "year", "timezone": "Asia/Shanghai"})

    assert "business_model.stat_date >= '2026-01-01'" in sql
    assert "business_model.stat_date < '2027-01-01'" in sql


def test_sql_compiler_renders_recent_months_as_rolling_window():
    sql = _compile_time_filter(
        {
            "kind": "relative_range",
            "unit": "month",
            "amount": 3,
            "anchor": "today",
            "include_current": True,
            "timezone": "Asia/Shanghai",
        }
    )

    assert "business_model.stat_date >= '2026-04-05'" in sql
    assert "business_model.stat_date <= '2026-07-04'" in sql


def test_sql_compiler_renders_recent_months_with_month_end_clamp():
    # 月底日期做月份平移时需要按目标月天数收敛，不能溢出。
    sql = _compile_time_filter(
        {
            "kind": "relative_range",
            "unit": "month",
            "amount": 1,
            "anchor": "today",
            "include_current": True,
            "timezone": "Asia/Shanghai",
        },
        today=date(2026, 3, 31),
    )

    assert "business_model.stat_date >= '2026-03-01'" in sql
    assert "business_model.stat_date <= '2026-03-31'" in sql


def test_sql_compiler_rejects_unrenderable_time_ast_instead_of_stringifying():
    with pytest.raises(ValueError, match="SEMANTIC_SQL_TIME_RANGE_UNSUPPORTED"):
        _compile_time_filter({"kind": "unsupported", "raw": "农历新年", "timezone": "Asia/Shanghai"})


def test_sql_compiler_rejects_zero_amount_relative_range():
    with pytest.raises(ValueError, match="SEMANTIC_SQL_TIME_RANGE_UNSUPPORTED"):
        _compile_time_filter(
            {
                "kind": "relative_range",
                "unit": "month",
                "amount": 0,
                "anchor": "today",
                "include_current": True,
                "timezone": "Asia/Shanghai",
            }
        )
