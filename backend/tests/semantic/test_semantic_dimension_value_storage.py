from apps.semantic.models import (
    SemanticDataset,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticDomain,
    SemanticModel,
)
from apps.semantic.service import SemanticSchemaBuilder
from apps.semantic.storage_sync import (
    build_dim_value_maps_from_storage,
    dimension_values_from_maps,
)


def test_dimension_values_from_maps_supports_legacy_shapes():
    dimension = SemanticDimension(
        id=20,
        oid=1,
        model_id=9,
        name="性别",
        biz_name="gender",
        dim_value_maps=[
            {"value": "女", "bizName": "女性", "alias": ["女士", "female"]},
            {"tech_name": "男", "biz_name": "男性", "alias": ["先生"]},
        ],
    )

    values = dimension_values_from_maps(dimension)

    assert [(item.value, item.display_value, item.alias) for item in values] == [
        ("女", "女性", ["女士", "female"]),
        ("男", "男性", ["先生"]),
    ]


def test_build_dim_value_maps_from_storage_keeps_schema_value_map_shape():
    values = [
        SemanticDimensionValue(
            oid=1,
            dimension_id=20,
            model_id=9,
            value="女",
            display_value="女性",
            biz_name="female",
            alias=["女士"],
        )
    ]

    assert build_dim_value_maps_from_storage(values) == [
        {"value": "女", "bizName": "女性", "biz_name": "female", "alias": ["女士"]}
    ]


def test_schema_builder_prefers_storage_dimension_values():
    domain = SemanticDomain(id=1, oid=1, name="客户域", biz_name="customer")
    model = SemanticModel(id=9, oid=1, domain_id=1, datasource_id=7, name="客户模型", biz_name="customer_model")
    dimension = SemanticDimension(
        id=20,
        oid=1,
        model_id=9,
        name="性别",
        biz_name="gender",
        dim_value_maps=[{"value": "F", "bizName": "旧女性", "alias": ["old"]}],
    )
    value = SemanticDimensionValue(
        id=30,
        oid=1,
        dimension_id=20,
        model_id=9,
        value="女",
        display_value="女性",
        biz_name="female",
        alias=["女士", "female"],
    )
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
        metrics=[],
        dimensions=[dimension],
        terms=[],
        dimension_values=[value],
    )

    assert schema.dimension_values[0].alias == ["女性", "女士", "female"]
    assert schema.dimension_values[0].schema_value_maps == [
        {"value": "女", "bizName": "女性", "biz_name": "female", "alias": ["女士", "female"]}
    ]
