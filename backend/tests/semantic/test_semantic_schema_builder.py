from apps.datasource import DatasourceRecord
from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDimension,
    SemanticDomain,
    SemanticMetric,
    SemanticModel,
    SemanticModelRelation,
    SemanticTerm,
)
from apps.semantic.services.builders.schema_builder import (
    SemanticSchemaBuilder,
    build_ontology_from_schema,
)


def test_schema_builder_exposes_dataset_selected_metrics_and_dimensions():
    domain = SemanticDomain(id=1, oid=1, name="销售域", biz_name="sales")
    model = SemanticModel(
        id=10,
        oid=1,
        domain_id=1,
        datasource_id=7,
        name="档口流量模型",
        biz_name="stall_traffic",
        source_type="TABLE",
        filter_sql="is_deleted = 0",
        depends=[{"modelId": 11, "joinType": "left", "leftKey": "stall_id", "rightKey": "stall_id"}],
        model_detail={
            "queryType": "table_query",
            "tableQuery": {"table": "stall_traffic_1d"},
            "fields": [{"fieldName": "visit_uv", "dataType": "BIGINT"}],
            "identifiers": [{"name": "档口", "bizName": "stall_id", "type": "primary"}],
            "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
        },
    )
    metric = SemanticMetric(
        id=100,
        oid=1,
        model_id=10,
        name="访问人数",
        biz_name="visit_uv",
        alias=["UV", "访客数"],
        default_agg="SUM",
        type_params={
            "metricDefineType": "MEASURE",
            "metricDefineByMeasureParams": {
                "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
                "expr": "visit_uv",
            },
        },
    )
    dimension = SemanticDimension(
        id=200,
        oid=1,
        model_id=10,
        name="档口",
        biz_name="stall_id",
        alias=["摊位"],
        dim_value_maps=[{"value": "1", "bizName": "1号档口", "alias": ["一号档口"]}],
    )
    term = SemanticTerm(id=300, oid=1, domain_id=1, name="人气", alias=["热度"], description="访问相关指标")
    dataset = SemanticDataset(
        id=20,
        oid=1,
        domain_id=1,
        name="档口经营分析",
        biz_name="stall_bi",
        data_set_detail={
            "dataSetModelConfigs": [
                {"id": 10, "includesAll": False, "metrics": [100], "dimensions": [200]}
            ]
        },
    )

    schema = SemanticSchemaBuilder().build_from_assets(
        dataset=dataset,
        domain=domain,
        models=[model],
        metrics=[metric],
        dimensions=[dimension],
        terms=[term],
    )

    assert schema.data_set.name == "档口经营分析"
    assert [item.name for item in schema.metrics] == ["访问人数"]
    assert [item.name for item in schema.dimensions] == ["档口"]
    assert schema.dimension_values[0].alias == ["1号档口", "一号档口"]
    assert schema.terms[0].name == "人气"
    assert schema.metrics[0].type_params["metricDefineByMeasureParams"]["measures"][0]["datasourceId"] == 10
    assert schema.models == [
        {
            "id": 10,
            "name": "档口流量模型",
                "biz_name": "stall_traffic",
                "datasource_id": 7,
                "source_type": "TABLE",
                "default_time_field": None,
                "queryType": "table_query",
            "tableQuery": "stall_traffic_1d",
            "sqlQuery": "",
            "filterSql": "is_deleted = 0",
            "depends": [{"modelId": 11, "joinType": "left", "leftKey": "stall_id", "rightKey": "stall_id"}],
            "fields": [{"fieldName": "visit_uv", "dataType": "BIGINT"}],
            "identifiers": [{"name": "档口", "bizName": "stall_id", "type": "primary"}],
            "dimensions": [],
            "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM", "datasourceId": 10}],
        }
    ]
    assert schema.metrics[0].fields == ["visit_uv"]


def test_schema_builder_only_exposes_terms_in_dataset_scope():
    domain = SemanticDomain(id=1, oid=1, name="销售域", biz_name="sales")
    dataset = SemanticDataset(
        id=20,
        oid=1,
        domain_id=1,
        name="销售数据集",
        biz_name="sales_dataset",
    )
    global_term = SemanticTerm(
        id=100,
        oid=1,
        domain_id=1,
        name="全域术语",
    )
    scoped_term = SemanticTerm(
        id=101,
        oid=1,
        domain_id=1,
        name="当前数据集术语",
        related_datasets=[20],
    )
    other_term = SemanticTerm(
        id=102,
        oid=1,
        domain_id=1,
        name="其他数据集术语",
        related_datasets=[21],
    )

    schema = SemanticSchemaBuilder().build_from_assets(
        dataset=dataset,
        domain=domain,
        models=[],
        metrics=[],
        dimensions=[],
        terms=[global_term, scoped_term, other_term],
    )

    assert [term.id for term in schema.terms] == [100, 101]


def test_schema_builder_adds_database_and_model_relations_for_runtime_assets():
    domain = SemanticDomain(id=1, oid=1, name="销售域", biz_name="sales")
    datasource = DatasourceRecord(
        id=7,
        oid=1,
        name="本地 MySQL",
        type="mysql",
        type_name="MySQL",
        configuration='{"databaseVersion": "8.0"}',
        create_by=1,
        recommended_config=1,
    )
    traffic_model = SemanticModel(
        id=10,
        oid=1,
        domain_id=1,
        datasource_id=7,
        name="档口流量模型",
        biz_name="stall_traffic",
        source_type="TABLE",
        model_detail={
            "queryType": "table_query",
            "tableQuery": {"table": "stall_traffic_1d"},
            "fields": [{"fieldName": "stall_id", "dataType": "BIGINT"}],
            "identifiers": [{"name": "档口", "bizName": "stall_id", "fieldName": "stall_id", "type": "primary"}],
            "dimensions": [{"name": "档口", "bizName": "stall_id", "expr": "stall_id", "type": "primary_key"}],
            "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
        },
    )
    stall_model = SemanticModel(
        id=11,
        oid=1,
        domain_id=1,
        datasource_id=7,
        name="档口信息模型",
        biz_name="stall_info",
        source_type="SQL",
        model_detail={
            "queryType": "sql_query",
            "sqlQuery": {"sql": "select stall_id, region from stall_info"},
            "fields": [{"fieldName": "region", "dataType": "VARCHAR"}],
            "identifiers": [{"name": "档口", "bizName": "stall_id", "fieldName": "stall_id", "type": "primary"}],
            "dimensions": [{"name": "区域", "bizName": "region", "expr": "region", "type": "categorical"}],
            "measures": [],
        },
    )
    metric = SemanticMetric(
        id=100,
        oid=1,
        model_id=10,
        name="访问人数",
        biz_name="visit_uv",
        default_agg="SUM",
        type_params={
            "metricDefineType": "MEASURE",
            "metricDefineByMeasureParams": {
                "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
                "expr": "visit_uv",
            },
        },
    )
    dimension = SemanticDimension(
        id=200,
        oid=1,
        model_id=11,
        name="区域",
        biz_name="region",
        expr="region",
        type="categorical",
    )
    relation = SemanticModelRelation(
        id=300,
        oid=1,
        domain_id=1,
        left_model_id=10,
        right_model_id=11,
        join_type="left join",
        join_conditions=[{"leftField": "stall_id", "operator": "=", "rightField": "stall_id"}],
    )
    dataset = SemanticDataset(
        id=20,
        oid=1,
        domain_id=1,
        name="档口经营分析",
        biz_name="stall_bi",
        data_set_detail={
            "dataSetModelConfigs": [
                {"id": 10, "includesAll": True, "metrics": [], "dimensions": []},
                {"id": 11, "includesAll": True, "metrics": [], "dimensions": []},
            ]
        },
    )

    schema = SemanticSchemaBuilder().build_from_assets(
        dataset=dataset,
        domain=domain,
        models=[traffic_model, stall_model],
        metrics=[metric],
        dimensions=[dimension],
        terms=[],
        datasources=[datasource],
        model_relations=[relation],
    )
    ontology = build_ontology_from_schema(schema)

    assert schema.database_type == "mysql"
    assert schema.database_version == "8.0"
    assert schema.models[0]["tableQuery"] == "stall_traffic_1d"
    assert schema.models[1]["sqlQuery"] == "select stall_id, region from stall_info"
    assert schema.model_relations[0].left == "stall_traffic"
    assert schema.model_relations[0].right == "stall_info"
    assert schema.model_relations[0].join_condition == [["stall_id", "=", "stall_id"]]
    assert ontology.model_map["stall_traffic"]["id"] == 10
    assert ontology.metric_map["stall_traffic"][0].biz_name == "visit_uv"
    assert ontology.dimension_map["stall_info"][0].biz_name == "region"
    assert ontology.join_relations[0].join_type == "left join"


def test_schema_builder_skips_inactive_assets():
    domain = SemanticDomain(id=1, oid=1, name="销售域", biz_name="sales")
    model = SemanticModel(id=10, oid=1, domain_id=1, datasource_id=7, name="模型", biz_name="model")
    inactive_metric = SemanticMetric(
        id=100,
        oid=1,
        model_id=10,
        name="已删除指标",
        biz_name="deleted_metric",
        status=0,
    )
    inactive_dimension = SemanticDimension(
        id=200,
        oid=1,
        model_id=10,
        name="已删除维度",
        biz_name="deleted_dimension",
        status=0,
    )
    inactive_term = SemanticTerm(id=300, oid=1, domain_id=1, name="已删除术语", status=0)
    dataset = SemanticDataset(
        id=20,
        oid=1,
        domain_id=1,
        name="数据集",
        biz_name="dataset",
        data_set_detail={
            "dataSetModelConfigs": [
                {"id": 10, "includesAll": True, "metrics": [], "dimensions": []}
            ]
        },
    )

    schema = SemanticSchemaBuilder().build_from_assets(
        dataset=dataset,
        domain=domain,
        models=[model],
        metrics=[inactive_metric],
        dimensions=[inactive_dimension],
        terms=[inactive_term],
    )

    assert schema.metrics == []
    assert schema.dimensions == []
    assert schema.terms == []
