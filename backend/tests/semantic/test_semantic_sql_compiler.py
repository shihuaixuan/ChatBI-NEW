from apps.semantic.schemas import DatasetSchema, JoinRelation, SchemaElement
from apps.semantic.sql_compiler import SemanticSQLCompiler, SemanticSQLCompileRequest


def test_semantic_sql_compile_request_keeps_repair_context():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="档口经营分析",
            id=20,
            name="档口经营分析",
            biz_name="stall_bi",
            type="DATASET",
        )
    )

    request = SemanticSQLCompileRequest(
        schema=schema,
        repair_context={"action": "regenerate_sql", "failed_sql": "select * from missing_table"},
    )

    assert request.repair_context == {"action": "regenerate_sql", "failed_sql": "select * from missing_table"}


def test_semantic_sql_compiler_switches_metric_to_candidate_table_during_repair():
    schema = DatasetSchema(
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
                "name": "旧流量模型",
                "biz_name": "missing_traffic",
                "tableQuery": "missing_table",
                "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
            },
            {
                "id": 11,
                "name": "档口流量模型",
                "biz_name": "stall_traffic",
                "tableQuery": "stall_traffic_1d",
                "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
            },
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
            ),
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=11,
                id=101,
                name="访问人数",
                biz_name="visit_uv",
                type="METRIC",
                default_agg="SUM",
                fields=["visit_uv"],
            ),
        ],
    )

    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=schema,
            question="今日访问人数",
            metric_ids=[100],
            repair_context={
                "action": "regenerate_sql",
                "failed_sql": "select sum(missing_traffic.visit_uv) as visit_uv from missing_table missing_traffic",
                "candidate_tables": ["stall_traffic_1d"],
            },
        )
    )

    assert result.tables == ["stall_traffic_1d"]
    assert result.sql == "select sum(stall_traffic.visit_uv) as visit_uv from stall_traffic_1d stall_traffic"


def test_semantic_sql_compiler_builds_joined_metric_query_from_schema_assets():
    schema = DatasetSchema(
        database_type="mysql",
        database_version="8.0",
        data_set=SchemaElement(data_set_id=20, data_set_name="档口经营分析", id=20, name="档口经营分析", biz_name="stall_bi", type="DATASET"),
        models=[
            {
                "id": 10,
                "name": "档口流量模型",
                "biz_name": "stall_traffic",
                "tableQuery": "stall_traffic_1d",
                "sqlQuery": "",
                "filterSql": "is_deleted = 0",
                "dimensions": [{"name": "档口", "bizName": "stall_id", "expr": "stall_id", "type": "primary_key"}],
                "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
            },
            {
                "id": 11,
                "name": "档口信息模型",
                "biz_name": "stall_info",
                "tableQuery": "stall_info",
                "sqlQuery": "",
                "filterSql": "",
                "dimensions": [{"name": "区域", "bizName": "region", "expr": "region", "type": "categorical"}],
                "measures": [],
            },
        ],
        model_relations=[
            JoinRelation(
                id=300,
                left="stall_traffic",
                right="stall_info",
                join_type="left join",
                join_condition=[["stall_id", "=", "stall_id"]],
            )
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
                type_params={
                    "metricDefineType": "MEASURE",
                    "metricDefineByMeasureParams": {
                        "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
                        "expr": "visit_uv",
                    },
                },
            )
        ],
        dimensions=[
            SchemaElement(data_set_id=20, data_set_name="档口经营分析", model=11, id=200, name="区域", biz_name="region", type="DIMENSION"),
        ],
    )

    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=schema,
            question="各区域访问人数",
            metric_ids=[100],
            dimension_ids=[200],
        )
    )

    assert result.tables == ["stall_traffic_1d", "stall_info"]
    assert result.metrics == ["visit_uv"]
    assert result.dimensions == ["region"]
    assert result.sql == (
        "select stall_info.region as region, sum(stall_traffic.visit_uv) as visit_uv "
        "from stall_traffic_1d stall_traffic "
        "left join stall_info stall_info on stall_traffic.stall_id = stall_info.stall_id "
        "where stall_traffic.is_deleted = 0 "
        "group by stall_info.region"
    )


def test_semantic_sql_compiler_matches_assets_from_question_when_slots_are_empty():
    schema = DatasetSchema(
        data_set=SchemaElement(data_set_id=20, data_set_name="档口经营分析", id=20, name="档口经营分析", biz_name="stall_bi", type="DATASET"),
        models=[
            {
                "id": 10,
                "name": "档口流量模型",
                "biz_name": "stall_traffic",
                "tableQuery": "stall_traffic_1d",
                "dimensions": [{"name": "统计日期", "bizName": "stat_date", "expr": "stat_date", "type": "partition_time"}],
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
                alias=["日期"],
            )
        ],
    )

    result = SemanticSQLCompiler().compile(SemanticSQLCompileRequest(schema=schema, question="按日期看访问人数"))

    assert result.sql == (
        "select stall_traffic.stat_date as stat_date, sum(stall_traffic.visit_uv) as visit_uv "
        "from stall_traffic_1d stall_traffic "
        "group by stall_traffic.stat_date"
    )


def test_semantic_sql_compiler_applies_dimension_filter_slots_for_detail_query():
    schema = DatasetSchema(
        data_set=SchemaElement(data_set_id=2, data_set_name="客户相关的数据集", id=2, name="客户相关的数据集", biz_name="customer_dataset", type="DATASET"),
        models=[
            {
                "id": 4,
                "name": "客户模型",
                "biz_name": "customer_model",
                "tableQuery": "customers",
                "dimensions": [
                    {"name": "客户名称", "bizName": "customer_name", "expr": "customer_name", "type": "categorical"},
                    {"name": "性别", "bizName": "gender", "expr": "gender", "type": "categorical"},
                    {"name": "会员等级", "bizName": "member_level", "expr": "member_level", "type": "categorical"},
                    {"name": "注册时间", "bizName": "register_date", "expr": "register_date", "type": "partition_time"},
                ],
                "measures": [],
            }
        ],
        dimensions=[
            SchemaElement(data_set_id=2, data_set_name="客户相关的数据集", model=4, id=14, name="客户名称", biz_name="customer_name", type="DIMENSION"),
            SchemaElement(data_set_id=2, data_set_name="客户相关的数据集", model=4, id=15, name="性别", biz_name="gender", type="DIMENSION"),
            SchemaElement(data_set_id=2, data_set_name="客户相关的数据集", model=4, id=16, name="会员等级", biz_name="member_level", type="DIMENSION"),
            SchemaElement(data_set_id=2, data_set_name="客户相关的数据集", model=4, id=18, name="注册时间", biz_name="register_date", type="DIMENSION"),
        ],
    )

    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=schema,
            question="性别为女的用户的名称与会员等级和注册时间",
            slots={
                "dimensions": [
                    {"asset_type": "DIMENSION", "asset_id": 14, "display_name": "客户名称"},
                    {"asset_type": "DIMENSION", "asset_id": 16, "display_name": "会员等级"},
                    {"asset_type": "DIMENSION", "asset_id": 18, "display_name": "注册时间"},
                ],
                "filters": [
                    {"asset_type": "DIMENSION", "asset_id": 15, "display_name": "性别", "operator": "=", "value": "女"}
                ],
            },
        )
    )

    assert result.sql == (
        "select customer_model.customer_name as customer_name, customer_model.member_level as member_level, "
        "customer_model.register_date as register_date from customers customer_model "
        "where customer_model.gender = '女'"
    )


def test_semantic_sql_compiler_renders_derived_metric_order_and_limit():
    schema = DatasetSchema(
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
                "name": "档口订单",
                "biz_name": "stall_order",
                "tableQuery": "fct_stall_order_daily",
                "dimensions": [{"name": "档口", "bizName": "stall_id", "expr": "stall_id"}],
                "measures": [
                    {"name": "销售GMV", "bizName": "gmv_sale", "expr": "gmv_sale", "agg": "SUM"},
                    {"name": "销售订单数", "bizName": "order_cnt_sale", "expr": "order_cnt_sale", "agg": "SUM"},
                ],
            }
        ],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=100,
                name="销售客单价",
                biz_name="aov_sale",
                type="METRIC",
                default_agg="NONE",
                type_params={
                    "metricDefineType": "FIELD",
                    "metricDefineByMeasureParams": {
                        "expr": "SUM(gmv_sale) / NULLIF(SUM(order_cnt_sale), 0)",
                    },
                },
            )
        ],
        dimensions=[
            SchemaElement(
                data_set_id=20,
                data_set_name="档口经营分析",
                model=10,
                id=200,
                name="档口",
                biz_name="stall_id",
                type="DIMENSION",
            )
        ],
    )

    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=schema,
            metric_ids=[100],
            dimension_ids=[200],
            order_by=[{"asset_type": "METRIC", "asset_id": 100, "direction": "desc"}],
            limit=5,
        )
    )

    assert "SUM(gmv_sale) / NULLIF(SUM(order_cnt_sale), 0) as aov_sale" in result.sql
    assert "group by stall_order.stall_id" in result.sql
    assert result.sql.endswith("order by aov_sale desc limit 5")


def test_semantic_sql_compiler_detail_mode_projects_metric_without_aggregation():
    schema = DatasetSchema(
        data_set=SchemaElement(data_set_id=20, data_set_name="档口经营分析", id=20, name="档口经营分析", biz_name="stall_bi", type="DATASET"),
        models=[
            {
                "id": 10,
                "name": "档口流量模型",
                "biz_name": "stall_traffic",
                "tableQuery": "stall_traffic_1d",
                "dimensions": [{"name": "店铺", "bizName": "stall_id", "expr": "stall_id"}],
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
                name="店铺",
                biz_name="stall_id",
                type="DIMENSION",
            )
        ],
    )

    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=schema,
            slots={
                "metrics": [{"asset_type": "METRIC", "asset_id": 100}],
                "dimensions": [{"asset_type": "DIMENSION", "asset_id": 200}],
            },
            select_mode="detail",
            limit=20,
        )
    )

    assert result.sql == (
        "select stall_traffic.stall_id as stall_id, stall_traffic.visit_uv as visit_uv "
        "from stall_traffic_1d stall_traffic limit 20"
    )


def test_semantic_sql_compiler_renders_metric_having_condition():
    schema = DatasetSchema(
        data_set=SchemaElement(data_set_id=20, data_set_name="档口经营分析", id=20, name="档口经营分析", biz_name="stall_bi", type="DATASET"),
        models=[
            {
                "id": 10,
                "name": "档口流量模型",
                "biz_name": "stall_traffic",
                "tableQuery": "stall_traffic_1d",
                "dimensions": [{"name": "店铺", "bizName": "stall_id", "expr": "stall_id"}],
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
                name="店铺",
                biz_name="stall_id",
                type="DIMENSION",
            )
        ],
    )

    result = SemanticSQLCompiler().compile(
        SemanticSQLCompileRequest(
            schema=schema,
            metric_ids=[100],
            dimension_ids=[200],
            having=[{"asset_type": "METRIC", "asset_id": 100, "operator": ">", "value": 100}],
        )
    )

    assert result.sql == (
        "select stall_traffic.stall_id as stall_id, sum(stall_traffic.visit_uv) as visit_uv "
        "from stall_traffic_1d stall_traffic "
        "group by stall_traffic.stall_id having sum(stall_traffic.visit_uv) > 100"
    )
