from sqlmodel import SQLModel

from apps.semantic.models import orm as semantic_orm
from apps.semantic.models.orm import (
    BusinessEntity,
    LogicalDimension,
    MetricDimensionCapability,
    SemanticDataset,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticDomain,
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelMeasure,
)


def test_semantic_orm_package_exports_and_registers_all_tables():
    expected_exports = {
        "SemanticAssetAlias",
        "SemanticAssetRelation",
        "SemanticDataset",
        "SemanticDatasetAsset",
        "SemanticDatasetModelConfig",
        "SemanticDatasetInstruction",
        "SemanticDimension",
        "SemanticDimensionValue",
        "SemanticDomain",
        "SemanticMetric",
        "SemanticModel",
        "SemanticModelField",
        "SemanticModelMeasure",
        "SemanticModelRelation",
        "SemanticTerm",
        "BusinessEntity",
        "DimensionHierarchy",
        "DimensionHierarchyLevel",
        "LogicalDimension",
        "MetricDimensionCapability",
        "MetricRelationship",
        "MetricRelationshipDimension",
    }
    expected_tables = {
        "headless_asset_alias",
        "headless_asset_relation",
        "headless_dataset",
        "headless_dataset_asset",
        "headless_dataset_model_config",
        "headless_dataset_instruction",
        "headless_dimension",
        "headless_dimension_value",
        "headless_domain",
        "headless_metric",
        "headless_model",
        "headless_model_field",
        "headless_model_measure",
        "headless_model_relation",
        "headless_term",
        "headless_business_entity",
        "headless_logical_dimension",
        "headless_metric_dimension_capability",
        "headless_dimension_hierarchy",
        "headless_dimension_hierarchy_level",
        "headless_metric_relationship",
        "headless_metric_relationship_dimension",
    }

    exported_tables = {
        getattr(semantic_orm, name).__tablename__
        for name in semantic_orm.__all__
    }

    assert set(semantic_orm.__all__) == expected_exports
    assert exported_tables == expected_tables
    assert expected_tables <= set(SQLModel.metadata.tables)


def test_semantic_contract_models_expose_isolated_defaults():
    """新契约对象的 JSON 字段必须在实例之间保持隔离。"""

    entity = BusinessEntity(
        oid=1,
        domain_id=2,
        name="店铺",
        biz_name="stall",
        key_type="bigint",
    )
    dimension = LogicalDimension(
        oid=1,
        domain_id=2,
        name="店铺ID",
        biz_name="stall_id",
        semantic_type="IDENTIFIER",
        value_type="BIGINT",
    )
    capability = MetricDimensionCapability(
        oid=1,
        metric_id=10,
        logical_dimension_id=20,
        binding_strategy="SAME_MODEL",
        target_model_id=30,
        aggregation_safety="SAFE",
        time_alignment_policy="NONE",
    )

    capability.usages.append("GROUP_BY")
    another_capability = MetricDimensionCapability(
        oid=1,
        metric_id=11,
        logical_dimension_id=20,
        binding_strategy="SAME_MODEL",
        target_model_id=30,
        aggregation_safety="SAFE",
        time_alignment_policy="NONE",
    )

    assert entity.version == 1
    assert dimension.version == 1
    assert capability.usages == ["GROUP_BY"]
    assert another_capability.usages == []


def test_semantic_models_expose_storage_fields():
    domain = SemanticDomain(
        oid=1,
        name="客户域",
        biz_name="customer",
        description="客户相关资产",
        owner="analyst",
        created_by="1",
        updated_by="2",
    )
    model = SemanticModel(
        oid=1,
        domain_id=1,
        datasource_id=7,
        name="客户模型",
        biz_name="customer_model",
        table_name="customers",
        sql_query="",
        primary_key=["customer_id"],
        model_grain=["customer_id"],
        default_time_field="register_date",
    )
    metric = SemanticMetric(
        oid=1,
        model_id=1,
        name="客户数",
        biz_name="customer_count",
        expr="customer_id",
        fields=["customer_id"],
        metric_refs=[],
        version=1,
        quality_status="VALID",
    )
    dimension = SemanticDimension(
        oid=1,
        model_id=1,
        name="注册时间",
        biz_name="register_date",
        field_name="register_date",
        is_default_time=True,
        time_granularities=["day", "month"],
    )
    dataset = SemanticDataset(
        oid=1,
        domain_id=1,
        name="客户数据集",
        biz_name="customer_ds",
        schema_version=1,
        index_version=0,
        default_model_id=1,
        default_time_dimension_id=1,
        owner="analyst",
    )

    assert domain.description == "客户相关资产"
    assert model.table_name == "customers"
    assert model.primary_key == ["customer_id"]
    assert metric.fields == ["customer_id"]
    assert dimension.is_default_time is True
    assert dataset.schema_version == 1


def test_semantic_model_field_measure_and_dimension_value_defaults_are_isolated():
    field = SemanticModelField(
        oid=1,
        model_id=1,
        field_name="pay_amount",
        name="支付金额",
        biz_name="pay_amount",
        expr="pay_amount",
        field_role="MEASURE",
        alias=["金额"],
    )
    measure = SemanticModelMeasure(
        oid=1,
        model_id=1,
        field_id=10,
        name="支付金额",
        biz_name="pay_amount",
        expr="pay_amount",
        agg="SUM",
        alias=["成交金额"],
    )
    value = SemanticDimensionValue(
        oid=1,
        dimension_id=20,
        model_id=1,
        value="女",
        display_value="女性",
        biz_name="female",
        alias=["女士", "female"],
    )

    assert field.alias == ["金额"]
    assert measure.agg == "SUM"
    assert value.enabled is True
    assert value.alias == ["女士", "female"]

    another_field = SemanticModelField(
        oid=1,
        model_id=1,
        field_name="gender",
        name="性别",
        biz_name="gender",
        expr="gender",
    )
    assert another_field.alias == []
