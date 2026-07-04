"""QueryPlan 绑定与计划驱动 SQL 生成的回归测试（Step 2）。

golden 对比：对既有可表达的查询形态，"经 bind_query_plan 计划编译"必须与
"直接从 knowledge 推导编译"（旧路径）产出逐字节相同的 SQL。
新增能力（时间分桶 A7、维值过滤翻译 B9）单独断言新行为。
"""

import pytest

from apps.chatbi_workflow.capabilities.adapters.sql import SqlAdapter
from apps.chatbi_workflow.capabilities.planning import QueryPlanBinder
from apps.headless.schemas import DataSetSchema, SchemaElement


class FakeHeadlessSchemaBuilder:
    def __init__(self, schema: DataSetSchema) -> None:
        self.schema = schema

    def build_dataset_schema(self, oid: int, dataset_id: int) -> DataSetSchema:
        return self.schema


def _schema(database_type: str | None = None) -> DataSetSchema:
    return DataSetSchema(
        database_type=database_type,
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="档口经营分析",
            id=20,
            name="档口经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[
            {
                "id": 10,
                "name": "档口流量模型",
                "biz_name": "stall_traffic",
                "datasource_id": 5,
                "tableQuery": "stall_traffic_1d",
                "dimensions": [
                    {"name": "统计日期", "bizName": "stat_date", "expr": "stat_date"},
                    {"name": "店铺名称", "bizName": "shop_name", "expr": "shop_name"},
                    {"name": "城市", "bizName": "city", "expr": "city"},
                ],
                "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
            }
        ],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=100,
                name="访问人数",
                biz_name="visit_uv",
                type="METRIC",
                default_agg="SUM",
                fields=["visit_uv"],
            )
        ],
        dimensions=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=200,
                name="统计日期",
                biz_name="stat_date",
                type="DIMENSION",
                ext_info={"dimension_type": "time", "is_default_time": True},
            ),
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=210,
                name="店铺名称",
                biz_name="shop_name",
                type="DIMENSION",
            ),
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=220,
                name="城市",
                biz_name="city",
                type="DIMENSION",
            ),
        ],
    )


def _metric_binding() -> dict:
    return {"asset_type": "METRIC", "asset_id": 100, "display_name": "访问人数", "biz_name": "visit_uv"}


def _time_filter_binding() -> dict:
    return {
        "asset_type": "DIMENSION",
        "asset_id": 200,
        "display_name": "统计日期",
        "biz_name": "stat_date",
        "operator": "=",
        "value": {"kind": "single_date", "anchor": "today", "offset_days": 0, "timezone": "Asia/Shanghai"},
    }


def _request(variables: dict) -> dict:
    return {
        "request": {"question": "档口访问人数", "dataset_id": 20, "tenant_id": 1, "user_id": 2},
        "variables": variables,
    }


def _generate_sql(variables: dict, database_type: str | None = None) -> str:
    adapter = SqlAdapter(schema_builder=FakeHeadlessSchemaBuilder(_schema(database_type)))
    return adapter.generate(_request(variables))["sql"]


GOLDEN_CASES = {
    "metric_with_time_filter": {
        "knowledge": {
            "hit": True,
            "status": "hit",
            "slot_bindings": {
                "metrics": [_metric_binding()],
                "group_dimensions": [],
                "time_filters": [_time_filter_binding()],
                "value_filters": [],
                "dimension_filters": [],
            },
            "selected_assets": {"metrics": [], "dimensions": [], "values": [], "terms": []},
        },
        "intent": {"intent_type": "metric_query", "query_shape": {"select_mode": "aggregate"}},
    },
    "ranking_with_group_and_limit": {
        "knowledge": {
            "hit": True,
            "status": "hit",
            "slot_bindings": {
                "metrics": [_metric_binding()],
                "group_dimensions": [
                    {"asset_type": "DIMENSION", "asset_id": 210, "display_name": "店铺名称", "biz_name": "shop_name"}
                ],
                "time_filters": [_time_filter_binding()],
                "value_filters": [],
                "dimension_filters": [],
            },
            "selected_assets": {"metrics": [], "dimensions": [], "values": [], "terms": []},
        },
        "intent": {
            "intent_type": "ranking_analysis",
            "query_shape": {"needs_group_by": True, "needs_order_by": True, "order_direction": "desc", "limit": 5},
        },
    },
    "legacy_filters_key": {
        "knowledge": {
            "hit": True,
            "status": "hit",
            "slot_bindings": {
                "metrics": [_metric_binding()],
                "dimensions": [
                    {"asset_type": "DIMENSION", "asset_id": 210, "display_name": "店铺名称", "biz_name": "shop_name"},
                    {"asset_type": "DIMENSION", "asset_id": 220, "display_name": "城市", "biz_name": "city"},
                ],
                "filters": [
                    {
                        "asset_type": "DIMENSION",
                        "asset_id": 220,
                        "display_name": "城市",
                        "operator": "=",
                        "value": "北京",
                    }
                ],
            },
            "selected_assets": {"metrics": [], "dimensions": [], "values": [], "terms": []},
        },
        "intent": {"intent_type": "metric_query", "query_shape": {"needs_group_by": True}},
    },
}


@pytest.mark.parametrize("case_name", sorted(GOLDEN_CASES))
def test_plan_path_produces_identical_sql_for_existing_shapes(case_name):
    case = GOLDEN_CASES[case_name]
    variables = {"knowledge": case["knowledge"], "intent": case["intent"]}

    legacy_sql = _generate_sql(variables)

    plan = QueryPlanBinder().bind(_request(variables))
    assert plan["status"] == "ready"
    planned_sql = _generate_sql({**variables, "plan": plan})

    assert planned_sql == legacy_sql


def test_binder_derives_time_bucket_from_intent_grain():
    case = GOLDEN_CASES["metric_with_time_filter"]
    variables = {
        "knowledge": case["knowledge"],
        "intent": {
            "intent_type": "trend_analysis",
            "query_shape": {"select_mode": "aggregate", "time_grain": "day"},
        },
    }

    plan = QueryPlanBinder().bind(_request(variables))

    assert plan["time"] == {"dimension_id": 200, "grain": "day"}


def test_trend_plan_compiles_time_bucketed_sql_instead_of_scalar_total():
    """A7 回归：趋势计划必须按时间分桶分组，而不是产出区间总量单行。"""

    case = GOLDEN_CASES["metric_with_time_filter"]
    intent = {"intent_type": "trend_analysis", "query_shape": {"select_mode": "aggregate", "time_grain": "day"}}
    variables = {"knowledge": case["knowledge"], "intent": intent}
    plan = QueryPlanBinder().bind(_request(variables))

    sql = _generate_sql({**variables, "plan": plan})

    # MySQL 方言：day 粒度转译为 DATE(...)；分组与默认时间升序排序必须存在。
    assert "DATE(stall_traffic.stat_date) as stat_date" in sql
    assert "group by DATE(stall_traffic.stat_date)" in sql
    assert "order by stat_date asc" in sql


def test_trend_plan_renders_dialect_specific_bucket_for_doris():
    case = GOLDEN_CASES["metric_with_time_filter"]
    intent = {"intent_type": "trend_analysis", "query_shape": {"select_mode": "aggregate", "time_grain": "month"}}
    variables = {"knowledge": case["knowledge"], "intent": intent}
    plan = QueryPlanBinder().bind(_request(variables))

    sql = _generate_sql({**variables, "plan": plan}, database_type="doris")

    assert "DATE_TRUNC(stall_traffic.stat_date, 'MONTH') as stat_date" in sql


def test_binder_translates_value_assets_into_dimension_filters():
    """B9 回归：命中的维值资产必须变成"父维度 = 标准值"的过滤条件。"""

    case = GOLDEN_CASES["metric_with_time_filter"]
    knowledge = {
        **case["knowledge"],
        "selected_assets": {
            "metrics": [],
            "dimensions": [],
            "terms": [],
            "values": [
                {
                    "asset_type": "VALUE",
                    "asset_id": 220,
                    "name": "城市",
                    "biz_name": "city",
                    "matched_text": "帝都",
                    "payload": {
                        "schema_value_maps": [{"value": "北京", "alias": ["帝都", "京城"]}],
                    },
                }
            ],
        },
    }
    variables = {"knowledge": knowledge, "intent": case["intent"]}

    plan = QueryPlanBinder().bind(_request(variables))

    value_filters = [item for item in plan["filters"] if item.get("asset_id") == 220]
    assert value_filters == [
        {
            "asset_type": "DIMENSION",
            "asset_id": 220,
            "display_name": "城市",
            "operator": "=",
            "value": "北京",
        }
    ]
    sql = _generate_sql({**variables, "plan": plan})
    assert "stall_traffic.city = '北京'" in sql


def test_binder_marks_plan_infeasible_when_knowledge_missed():
    plan = QueryPlanBinder().bind(_request({"knowledge": {"hit": False, "status": "missed"}, "intent": {}}))

    assert plan["status"] == "infeasible"
    assert plan["infeasible_reason"] == "knowledge_missed"


def test_sql_generate_rejects_infeasible_plan_explicitly():
    case = GOLDEN_CASES["metric_with_time_filter"]
    variables = {
        "knowledge": case["knowledge"],
        "intent": case["intent"],
        "plan": {"status": "infeasible", "infeasible_reason": "knowledge_missed"},
    }

    with pytest.raises(ValueError, match="QUERY_PLAN_INFEASIBLE"):
        _generate_sql(variables)
