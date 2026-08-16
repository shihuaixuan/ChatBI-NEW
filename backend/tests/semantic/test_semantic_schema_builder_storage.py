from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
    SemanticDatasetInstruction,
    SemanticDimension,
    SemanticDomain,
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelMeasure,
)
from apps.semantic.services.builders.schema_builder import SemanticSchemaBuilder


def test_schema_builder_prefers_storage_dataset_assets_and_model_fields():
    domain = SemanticDomain(id=1, oid=1, name="客户域", biz_name="customer", description="客户资产")
    model = SemanticModel(
        id=9,
        oid=1,
        domain_id=1,
        datasource_id=7,
        name="客户模型",
        biz_name="customer_model",
        table_name="customers",
        model_detail={
            "tableQuery": {"table": "legacy_customers"},
            "fields": [{"fieldName": "legacy_field"}],
            "measures": [{"bizName": "legacy_measure"}],
        },
    )
    metric = SemanticMetric(
        id=100,
        oid=1,
        model_id=9,
        name="客户数",
        biz_name="customer_count",
        fields=["customer_id"],
        default_agg="COUNT_DISTINCT",
    )
    skipped_metric = SemanticMetric(id=101, oid=1, model_id=9, name="跳过指标", biz_name="skip_metric")
    invalid_metric = SemanticMetric(
        id=102,
        oid=1,
        model_id=9,
        name="无效指标",
        biz_name="invalid_metric",
        fields=["missing_field"],
        quality_status="INVALID",
    )
    dimension = SemanticDimension(id=200, oid=1, model_id=9, name="会员等级", biz_name="member_level")
    skipped_dimension = SemanticDimension(id=201, oid=1, model_id=9, name="跳过维度", biz_name="skip_dimension")
    dataset = SemanticDataset(
        id=40,
        oid=1,
        domain_id=1,
        name="客户数据集",
        biz_name="customer_ds",
        data_set_detail={"dataSetModelConfigs": [{"id": 9, "includesAll": True, "metrics": [], "dimensions": []}]},
    )

    schema = SemanticSchemaBuilder().build_from_assets(
        dataset=dataset,
        domain=domain,
        models=[model],
        metrics=[metric, skipped_metric, invalid_metric],
        dimensions=[dimension, skipped_dimension],
        terms=[],
        model_fields=[
            SemanticModelField(
                id=1,
                oid=1,
                model_id=9,
                field_name="customer_id",
                name="客户ID",
                biz_name="customer_id",
                expr="customer_id",
                data_type="BIGINT",
                field_role="IDENTIFIER",
            )
        ],
        model_measures=[
            SemanticModelMeasure(
                id=2,
                oid=1,
                model_id=9,
                name="客户数",
                biz_name="customer_count",
                expr="customer_id",
                agg="COUNT_DISTINCT",
            )
        ],
        dataset_model_configs=[SemanticDatasetModelConfig(oid=1, dataset_id=40, model_id=9, includes_all=False)],
        dataset_assets=[
            SemanticDatasetAsset(oid=1, dataset_id=40, model_id=9, asset_type="METRIC", asset_id=100),
            SemanticDatasetAsset(oid=1, dataset_id=40, model_id=9, asset_type="DIMENSION", asset_id=200),
        ],
        instructions=[
            SemanticDatasetInstruction(
                oid=1,
                dataset_id=40,
                module="sql_generation",
                content="统一按元输出",
                version=2,
                enabled=True,
            ),
            SemanticDatasetInstruction(
                oid=1,
                dataset_id=40,
                module="sql_generation",
                content="旧规则",
                version=1,
                enabled=False,
            ),
        ],
    )

    assert schema.data_set.description == "客户资产"
    assert [item.biz_name for item in schema.metrics] == ["customer_count"]
    assert [item.biz_name for item in schema.dimensions] == ["member_level"]
    assert schema.models[0]["tableQuery"] == "customers"
    assert schema.models[0]["fields"][0]["fieldName"] == "customer_id"
    assert schema.models[0]["measures"][0]["id"] == 2
    assert schema.metrics[0].fields == ["customer_id"]
    assert schema.instructions == {"sql_generation": ["统一按元输出"]}
